"""Reading and changing the game's file list. No interface, no side effects
beyond the one function that writes.

LET IT DIE's executable carries a list of file names, each with the SHA-1 the
game expects that file to have, stored as a resource inside the .exe. A file
that is named in the list is checked when it loads; a file that is not named
there is never checked at all - which is why a great many mods have always
worked without anyone touching the executable.

Changing an entry's NAME, so a lookup for the real file name misses, switches
the check off for that file. That is all this does. It never changes a hash, it
never changes the game's code, and the file never changes size.

- KSFA
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

EXE_NAME = "BrgGame-Steam.exe"
EXE_RELATIVE = Path("Binaries") / "Win64" / EXE_NAME
GAME_FOLDER_NAME = "LET IT DIE"

RESOURCE_TYPE = 10          # RT_RCDATA
RESOURCE_ID = 1010          # the list lives in this one
HASH_BYTES = 20             # a raw SHA-1
SECTION_MARKER = b"+++"     # divides the list into sections, where present
CODE_SECTION = b".text"

BACKUP_NAME = EXE_NAME + ".stock-backup"
#: What a switched-off entry's last character is replaced with.
OFF_MARK = ord("X")
OFF_MARK_ALT = ord("Y")     # for a name that ended in X already


class Problem(Exception):
    """This is not the shape we expect. Nothing has been written."""


@dataclass(frozen=True)
class Entry:
    """One file the executable carries a hash for."""

    name: str           # lower case, as the game looks it up
    last_at: int        # file offset of the name's final character
    sha1: str           # what the game expects that file to hash to


# ---------------------------------------------------------------------------
# reading a Windows program
# ---------------------------------------------------------------------------

def _u16(raw: bytes, at: int) -> int:
    return struct.unpack_from("<H", raw, at)[0]


def _u32(raw: bytes, at: int) -> int:
    return struct.unpack_from("<I", raw, at)[0]


def _pe_offset(raw: bytes) -> int:
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise Problem("that file is not a Windows program")
    pe = _u32(raw, 0x3C)
    if len(raw) < pe + 24 or raw[pe:pe + 4] != b"PE\0\0":
        raise Problem("that file is not a valid Windows program")
    return pe


def _sections(raw: bytes) -> list[tuple[bytes, int, int, int, int]]:
    """(name, virtual address, virtual size, file offset, size on disk)."""
    pe = _pe_offset(raw)
    count = _u16(raw, pe + 6)
    first = pe + 24 + _u16(raw, pe + 20)
    out = []
    for i in range(count):
        at = first + i * 40
        out.append((raw[at:at + 8].rstrip(b"\0"), _u32(raw, at + 12),
                    _u32(raw, at + 8), _u32(raw, at + 20), _u32(raw, at + 16)))
    return out


def _file_offset(raw: bytes, rva: int, size: int = 1) -> int:
    for _name, virtual, vsize, on_disk, disk_size in _sections(raw):
        if virtual <= rva < virtual + max(vsize, disk_size):
            at = on_disk + (rva - virtual)
            if at + size <= len(raw):
                return at
    raise Problem("the program's layout does not make sense")


def code_fingerprint(raw: bytes) -> str:
    """A fingerprint of the program's CODE, which must never change.

    Two builds of the game differ here; a change of ours never may.
    """
    for name, _virtual, _vsize, on_disk, disk_size in _sections(raw):
        if name == CODE_SECTION:
            return hashlib.sha256(raw[on_disk:on_disk + disk_size]).hexdigest()
    raise Problem("the program has no code section")


def list_location(raw: bytes) -> tuple[int, int]:
    """Where the file list sits inside the program: (file offset, length).

    Found through the program's own resource directory, never a fixed offset,
    so this keeps working when the game updates.
    """
    pe = _pe_offset(raw)
    optional = pe + 24
    if _u16(raw, optional) != 0x20B:
        raise Problem("this only understands 64-bit programs")
    # Data directories start 112 bytes into a 64-bit optional header, and
    # resources are entry 2 - past export and import, eight bytes each.
    directory = _u32(raw, optional + 112 + 2 * 8)
    if not directory:
        raise Problem("the program carries no resources")
    root = _file_offset(raw, directory)

    def entries(at: int) -> list[tuple[int, int]]:
        named = _u16(raw, at + 12)
        numbered = _u16(raw, at + 14)
        return [(_u32(raw, at + 16 + i * 8), _u32(raw, at + 20 + i * 8))
                for i in range(named + numbered)]

    def descend(at: int, wanted: int) -> int:
        for key, offset in entries(at):
            if key == wanted:
                if not offset & 0x80000000:
                    raise Problem("the resource table is not the expected shape")
                return root + (offset & 0x7FFFFFFF)
        raise Problem("this program has no file list in it")

    by_id = descend(descend(root, RESOURCE_TYPE), RESOURCE_ID)
    _key, leaf = entries(by_id)[0]
    if leaf & 0x80000000:
        raise Problem("the resource table is nested deeper than expected")
    leaf += root
    length = _u32(raw, leaf + 4)
    return _file_offset(raw, _u32(raw, leaf), length), length


def read_list(raw: bytes) -> list[Entry]:
    """Every entry in the file list, in the order it is stored.

    Duplicates are kept: the list on this build names 8,266 files, of which
    8,038 are distinct, and a name that appears twice must be changed twice.
    """
    at, length = list_location(raw)
    end = at + length
    cursor = at
    out: list[Entry] = []
    while cursor < end:
        stop = raw.find(b"\0", cursor, end)
        if stop < 0:
            break
        name = raw[cursor:stop]
        cursor = stop + 1
        if not name:
            continue
        if name == SECTION_MARKER:
            break
        if cursor + HASH_BYTES > end:
            break
        try:
            text = name.decode("ascii")
        except UnicodeDecodeError:
            raise Problem("a name in the file list is not plain text") from None
        out.append(Entry(text.lower(), stop - 1, raw[cursor:cursor + HASH_BYTES].hex()))
        cursor += HASH_BYTES
    if not out:
        raise Problem("the file list is empty")
    return out


def expected_hash(raw: bytes, filename: str) -> str | None:
    """What the game expects this file to hash to, or None if it never checks."""
    wanted = Path(filename).name.lower()
    for entry in read_list(raw):
        if entry.name == wanted:
            return entry.sha1
    return None


# ---------------------------------------------------------------------------
# changing it
# ---------------------------------------------------------------------------

def switch_off(raw: bytes, targets: Sequence[Entry]) -> tuple[bytes, list[int]]:
    """The program with those entries' names changed, and nothing else.

    These checks are the reason this is allowed near an executable at all. Any
    one of them failing means nothing is returned and nothing can be written.
    """
    at, length = list_location(raw)
    out = bytearray(raw)
    meant = set()
    for entry in targets:
        if not at <= entry.last_at < at + length:
            raise Problem("an entry points outside the file list")
        out[entry.last_at] = (OFF_MARK if out[entry.last_at] != OFF_MARK
                              else OFF_MARK_ALT)
        meant.add(entry.last_at)
    changed = bytes(out)

    if len(changed) != len(raw):
        raise Problem("the change altered the file's size - refusing")
    if code_fingerprint(changed) != code_fingerprint(raw):
        raise Problem("the change altered the game's code - refusing")
    differs = [i for i in range(len(raw)) if raw[i] != changed[i]]
    if set(differs) != meant:
        raise Problem("bytes changed that were not meant to - refusing")
    if any(not at <= i < at + length for i in differs):
        raise Problem("a change landed outside the file list - refusing")
    if len(read_list(changed)) != len(read_list(raw)):
        raise Problem("the list no longer reads back correctly - refusing")
    return changed, differs


def switched_off(live: bytes, stock: bytes) -> list[str]:
    """Names in the stock program that the live one no longer answers to.

    This counts *names*, and 228 files on this build are listed twice, so it is
    always the smaller number. For how many entries were changed, which is what
    a byte count refers to, use ``switched_off_entries``.
    """
    return sorted({e.name for e in read_list(stock)} - {e.name for e in read_list(live)})


def switched_off_entries(live: bytes, stock: bytes) -> int:
    """How many entries differ, counting each listing separately.

    The two lists are the same length and the same order - only names change -
    so they can be compared position by position.
    """
    theirs, ours = read_list(stock), read_list(live)
    if len(theirs) != len(ours):
        raise Problem("these two are not the same build - their lists differ in length")
    return sum(1 for mine, yours in zip(theirs, ours) if mine.name != yours.name)


#: What a name looks like once we have been at it. The game itself only ever
#: names .upk, .ini and .usf files, so any of these is our own handiwork.
OFF_SUFFIXES = (".upx", ".upy", ".inx", ".iny", ".usx", ".usy")


def already_switched_off(raw: bytes) -> int:
    """How many entries look switched off, without needing the stock program.

    This is what stops a backup being taken from an executable that has already
    been changed - which would quietly become that machine's idea of "stock".
    """
    return sum(1 for entry in read_list(raw) if entry.name.endswith(OFF_SUFFIXES))


# ---------------------------------------------------------------------------
# the game on this machine
# ---------------------------------------------------------------------------

def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        winreg = None                                        # type: ignore[assignment]
    if winreg is not None:
        for key in (r"SOFTWARE\WOW6432Node\Valve\Steam", r"SOFTWARE\Valve\Steam"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, "InstallPath")[0]))
            except OSError:
                pass
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    roots.append(Path(program_files) / "Steam")

    found: list[Path] = []
    for root in roots:
        if root not in found:
            found.append(root)
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split('"')
            if len(parts) >= 4 and parts[1] == "path":
                candidate = Path(parts[3].replace("\\\\", "\\"))
                if candidate not in found:
                    found.append(candidate)
    return found


def find_game(given: str | os.PathLike[str] | None = None) -> Path:
    """The game's executable. Raises if it cannot be found."""
    if given:
        root = Path(given)
        exe = root if root.name.lower() == EXE_NAME.lower() else root / EXE_RELATIVE
        if not exe.is_file():
            raise Problem(f"no {EXE_NAME} under {root}")
        return exe
    for library in steam_libraries():
        exe = library / "steamapps" / "common" / GAME_FOLDER_NAME / EXE_RELATIVE
        if exe.is_file():
            return exe
    raise Problem("could not find LET IT DIE - point at its folder yourself")


def game_root(exe: Path) -> Path:
    return exe.parent.parent.parent


def game_is_running() -> bool:
    try:
        finished = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq " + EXE_NAME],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return EXE_NAME.lower() in (finished.stdout or "").lower()


# ---------------------------------------------------------------------------
# the copy to come back to
# ---------------------------------------------------------------------------

def backup_for(exe: Path, folder: Path) -> Path:
    return Path(folder) / BACKUP_NAME


def retire_backup(backup: Path) -> Path:
    """Move a backup from an older build aside, keeping it, and say where.

    It is never deleted: it is still the only stock copy of that build, and
    someone may want it. It just stops being what ``restore`` reaches for.
    """
    fingerprint = code_fingerprint(backup.read_bytes())[:8]
    retired = backup.with_name(f"{backup.name}.{fingerprint}")
    count = 1
    while retired.exists():
        count += 1
        retired = backup.with_name(f"{backup.name}.{fingerprint}-{count}")
    backup.rename(retired)
    return retired


def take_backup(exe: Path, folder: Path) -> tuple[Path, bool, Path | None]:
    """Copy the executable aside. Returns (path, whether it is new, retired).

    A backup from a different build of the game is NOT reused. The game updates
    and replaces its executable; keeping last version's copy would mean the way
    back puts an older program onto newer game files. That copy is moved aside
    and a fresh one taken from what is installed now.
    """
    backup = backup_for(exe, folder)
    live = exe.read_bytes()
    retired: Path | None = None
    if backup.exists():
        if backup_suits(backup.read_bytes(), live):
            return backup, False, None
        retired = retire_backup(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, backup)
    if backup.read_bytes() != live:
        backup.unlink()
        raise Problem("the backup did not come out identical - stopping")
    return backup, True, retired


@dataclass(frozen=True)
class BackupCheck:
    """What a backup is worth as the way back from what is installed."""

    same_build: bool
    #: How many entries the backup ITSELF has switched off. A stock copy has none.
    switched_off: int

    @property
    def stock(self) -> bool:
        return self.switched_off == 0

    @property
    def usable(self) -> bool:
        return self.same_build and self.stock

    def problem(self) -> str:
        """Why this backup is not the way back, in words, or ""."""
        if not self.stock:
            return (f"this backup has {self.switched_off:,} entries switched off in it, "
                    "so it is not a stock copy - restoring it would put the change back, "
                    "not take it away")
        if not self.same_build:
            return ("this backup is from a different build of the game - restoring it "
                    "would leave an executable older than the rest of the game")
        return ""


def check_backup(backup: bytes, live: bytes) -> BackupCheck:
    """Is this backup the untouched original of what is installed?

    Two ways it can fail. It can be from an older build, which
    ``backup_suits`` answers. Or it can be a copy of an executable that had
    already been switched off when it was taken - the trap this tool warns
    about elsewhere - and then it is worse than no backup at all: the status
    is read off it, so a stock game would be reported as switched off, and
    restoring it would switch the check off rather than back on.

    The names say which: the game never ships a name ending in ``.upx`` and
    the like, so any in there are this tool's own work.
    """
    return BackupCheck(same_build=backup_suits(backup, live),
                       switched_off=already_switched_off(backup))


def backup_suits(backup: bytes, live: bytes) -> bool:
    """Is this backup from the same build of the game as what is installed?

    A game update replaces the executable. Restoring last version's copy over
    it would leave the player on an executable older than their game, which is
    the one way this tool could do real damage.
    """
    return code_fingerprint(backup) == code_fingerprint(live)


def write_executable(exe: Path, data: bytes) -> None:
    """Replace the executable in one step, so it is never half-written."""
    temporary = exe.with_name(exe.name + ".new")
    temporary.write_bytes(data)
    os.replace(temporary, exe)


# ---------------------------------------------------------------------------
# what would break if the check came back
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Modified:
    """A file the game checks, which is no longer what the game expects."""

    path: Path
    name: str
    expected: str
    found: str


def sha1_of(path: Path, chunk: int = 4 << 20) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def scan_modified(stock: bytes, root: Path,
                  progress: Callable[[int, int, Path], bool] | None = None
                  ) -> list[Modified]:
    """Every checked file on disk that no longer matches its stock hash.

    This is the answer to "can I put the original executable back?" - each of
    these would be refused the moment the game could look its name up again.

    ``progress`` is called with (done, total, path) and may return False to
    stop early.
    """
    wanted = {}
    for entry in read_list(stock):
        wanted.setdefault(entry.name, entry.sha1)

    candidates = [p for p in Path(root).rglob("*")
                  if p.is_file() and p.name.lower() in wanted]
    out: list[Modified] = []
    total = len(candidates)
    for done, path in enumerate(candidates, start=1):
        if progress is not None and not progress(done, total, path):
            break
        name = path.name.lower()
        found = sha1_of(path)
        if found != wanted[name]:
            out.append(Modified(path, name, wanted[name], found))
    return out
