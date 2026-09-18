#!/usr/bin/env python3
"""Build a standalone Windows .exe with PyInstaller.

    python build.py              -> dist/LiD File Check/       (recommended)
    python build.py --onefile    -> dist/LiD File Check.exe

Run it with the same interpreter PySide6 is installed in.

The one thing this does that a bare `pyinstaller app.py` does not is look after
``backup/``. That folder holds the only stock copy of someone's game executable,
and PyInstaller deletes its whole output folder before each build. Losing it would
leave a player with a switched-off executable and no way back except Steam's
verify. So it is carried out of the way and put back.

- KSFA
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "LiD File Check"
ENTRY = "app.py"

#: What a rebuild must not destroy. ``backup`` is the important one: it is the
#: only stock copy of the game's executable on that machine.
RUNTIME_STATE = ("backup",)


def folder_size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def msvc_runtime_dlls() -> list[Path]:
    """The C++ runtime DLLs Qt links against, taken from the PySide6 wheel.

    Qt6Core/Gui/Widgets import MSVCP140.dll (and _1, _2), but PyInstaller only
    collects VCRUNTIME140*. On a machine without the Visual C++ redistributable
    the app would fail to start with a missing-DLL box. The PySide6 wheel ships
    its own copies so it can be deployed app-locally, and they are guaranteed to
    match the Qt build being bundled.
    """
    import PySide6

    package = Path(PySide6.__file__).parent
    names = ("msvcp140", "vcruntime140", "concrt140")
    return sorted(dll for dll in package.glob("*.dll")
                  if dll.name.lower().startswith(names))


def run_tests() -> bool:
    """The tests, before anything is shipped. They need no game to run."""
    print("Running the tests first")
    finished = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=ROOT)
    return finished.returncode == 0


def build(one_file: bool, icon: Path | None, keep_console: bool) -> Path:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--onefile" if one_file else "--onedir",
        "--console" if keep_console else "--windowed",
        # Nothing here needs unpacking at runtime; keep the bundle deterministic.
        "--noupx",
    ]
    runtime = msvc_runtime_dlls()
    for dll in runtime:
        command += ["--add-binary", f"{dll}{os.pathsep}PySide6"]
    if runtime:
        print(f"Bundling {len(runtime)} MSVC runtime DLL(s) so the build needs "
              "no redistributable")
    if icon is not None:
        command += ["--icon", str(icon)]
    command.append(str(ROOT / ENTRY))

    print("$ " + " ".join(f'"{c}"' if " " in c else c for c in command))
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"\nPyInstaller finished in {time.monotonic() - started:.0f}s")
    return ROOT / "dist" / (f"{APP_NAME}.exe" if one_file else APP_NAME)


def rescue_runtime_state(target_dir: Path, into: Path) -> list[str]:
    """Move a previous build's kept files somewhere safe. Returns what moved."""
    saved = []
    for name in RUNTIME_STATE:
        source = target_dir / name
        if source.exists():
            shutil.move(str(source), str(into / name))
            saved.append(name)
    return saved


def restore_runtime_state(target_dir: Path, saved_in: Path, names: list[str]) -> None:
    """Put it back, without overwriting anything the new build wrote."""
    for name in names:
        source = saved_in / name
        destination = target_dir / name
        if not source.exists():
            continue
        if destination.exists() and source.is_dir():
            for item in source.iterdir():
                target = destination / item.name
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                shutil.move(str(item), str(target))
            shutil.rmtree(source, ignore_errors=True)
        else:
            shutil.move(str(source), str(destination))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--onefile", action="store_true",
                        help="single .exe instead of a folder (smaller to send, "
                             "slower to start)")
    parser.add_argument("--icon", type=Path, help="path to an .ico file")
    parser.add_argument("--console", action="store_true",
                        help="keep the console window")
    parser.add_argument("--skip-tests", action="store_true",
                        help="build without running the tests first")
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install pyinstaller",
              file=sys.stderr)
        return 2
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 is not installed in this interpreter, so the build would "
              "have no window.\nRun:  pip install PySide6", file=sys.stderr)
        return 2
    if args.icon is not None and not args.icon.is_file():
        print(f"no icon at {args.icon}", file=sys.stderr)
        return 2
    if not args.skip_tests and not run_tests():
        print("\nThe tests did not pass, so nothing was built.\n"
              "This one writes to a game executable - build it from a green run "
              "or not at all.", file=sys.stderr)
        return 1

    # PyInstaller deletes its output folder, and for a folder build that is also
    # where the backup of someone's game executable lives. Carry it out of the
    # way and put it back: a rebuild is a developer action and must never cost
    # anyone their only way back to a stock game.
    target_dir = ROOT / "dist" / APP_NAME
    rescue = None
    saved: list[str] = []
    if target_dir.exists():
        rescue = tempfile.TemporaryDirectory(prefix="lid-file-check-")
        saved = rescue_runtime_state(target_dir, Path(rescue.name))
        if saved:
            print(f"Keeping {', '.join(saved)} safe while the build runs")

    try:
        built = build(args.onefile, args.icon, args.console)
    except subprocess.CalledProcessError as problem:
        print(f"\nPyInstaller failed ({problem.returncode})", file=sys.stderr)
        return problem.returncode
    finally:
        if rescue is not None:
            if saved and target_dir.exists():
                restore_runtime_state(target_dir, Path(rescue.name), saved)
                print(f"Put {', '.join(saved)} back")
            rescue.cleanup()

    print(f"\nBuilt: {built}")
    print(f"Size:  {folder_size_mb(built):.0f} MB")
    print("\nIt keeps its backup of the game executable in a 'backup' folder "
          "beside itself, so give people the whole folder, not just the .exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
