"""Tests for the file-list reading and the one thing here that writes.

These build a tiny Windows program of their own rather than leaning on a copy
of the game, so they run anywhere and say something when they fail.

- KSFA
"""

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hashtool import core


# ---------------------------------------------------------------------------
# a Windows program with a file list in it, built here
# ---------------------------------------------------------------------------

PE_AT = 0x80
OPTIONAL_SIZE = 240
TEXT_RVA = 0x1000
RSRC_RVA = 0x2000
TEXT_AT = 0x400


def build_list(pairs):
    """name -> sha1 hex, laid out the way the game stores it."""
    out = bytearray()
    for name, sha in pairs:
        out += name.encode("ascii") + b"\0" + bytes.fromhex(sha)
    return bytes(out)


def build_resource(payload):
    """The smallest resource tree that holds payload at type 10, id 1010."""
    root = bytearray()

    def directory(entries):
        block = bytearray(struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries)))
        for key, offset in entries:
            block += struct.pack("<II", key, offset)
        return block

    type_at = 0x18
    id_at = 0x30
    leaf_at = 0x48
    data_at = 0x58
    root += directory([(core.RESOURCE_TYPE, 0x80000000 | type_at)])
    assert len(root) == type_at
    root += directory([(core.RESOURCE_ID, 0x80000000 | id_at)])
    assert len(root) == id_at
    root += directory([(1033, leaf_at)])
    assert len(root) == leaf_at
    root += struct.pack("<IIII", RSRC_RVA + data_at, len(payload), 0, 0)
    assert len(root) == data_at
    root += payload
    return bytes(root)


def fake_exe(pairs, code=b"\x90" * 64):
    resource = build_resource(build_list(pairs))
    text_size = (len(code) + 0x1FF) & ~0x1FF
    rsrc_at = TEXT_AT + text_size
    rsrc_size = (len(resource) + 0x1FF) & ~0x1FF

    raw = bytearray(b"\0" * TEXT_AT)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, PE_AT)
    raw[PE_AT:PE_AT + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", raw, PE_AT + 4,
                     0x8664, 2, 0, 0, 0, OPTIONAL_SIZE, 0x22)

    optional = PE_AT + 24
    struct.pack_into("<H", raw, optional, 0x20B)
    struct.pack_into("<I", raw, optional + 112 + 2 * 8, RSRC_RVA)

    headers = optional + OPTIONAL_SIZE
    for i, (name, rva, vsize, at, size) in enumerate((
            (b".text", TEXT_RVA, len(code), TEXT_AT, text_size),
            (b".rsrc", RSRC_RVA, len(resource), rsrc_at, rsrc_size))):
        base = headers + i * 40
        raw[base:base + len(name)] = name
        struct.pack_into("<IIII", raw, base + 8, vsize, rva, size, at)

    raw += code + b"\0" * (text_size - len(code))
    raw += resource + b"\0" * (rsrc_size - len(resource))
    return bytes(raw)


A = "a" * 40
B = "b" * 40
C = "c" * 40


class ReadingTests(unittest.TestCase):
    def test_every_entry_comes_back_in_order(self):
        raw = fake_exe([("one.upk", A), ("two.upk", B)])
        names = [e.name for e in core.read_list(raw)]
        self.assertEqual(["one.upk", "two.upk"], names)
        self.assertEqual([A, B], [e.sha1 for e in core.read_list(raw)])

    def test_names_come_back_lower_case_because_that_is_how_they_are_looked_up(self):
        raw = fake_exe([("UI_Thing_SF.upk", A)])
        self.assertEqual("ui_thing_sf.upk", core.read_list(raw)[0].name)

    def test_a_name_listed_twice_is_kept_twice(self):
        raw = fake_exe([("same.upk", A), ("other.upk", B), ("same.upk", A)])
        self.assertEqual(3, len(core.read_list(raw)))

    def test_the_hash_a_file_is_expected_to_have(self):
        raw = fake_exe([("one.upk", A)])
        self.assertEqual(A, core.expected_hash(raw, "ONE.upk"))
        self.assertEqual(A, core.expected_hash(raw, r"C:\somewhere\One.UPK"))
        self.assertIsNone(core.expected_hash(raw, "missing.upk"))

    def test_reading_stops_at_a_section_marker(self):
        payload = build_list([("one.upk", A)]) + core.SECTION_MARKER + b"\0" \
            + build_list([("two.upk", B)])
        raw = fake_exe([("one.upk", A)])
        # rebuild by hand so the marker really sits in the resource
        at, _length = core.list_location(raw)
        spliced = raw[:at] + payload + raw[at + len(payload):]
        self.assertEqual(["one.upk"], [e.name for e in core.read_list(spliced)])

    def test_something_that_is_not_a_program_is_refused(self):
        with self.assertRaises(core.Problem):
            core.read_list(b"not a program at all")

    def test_a_program_with_no_list_is_refused(self):
        raw = bytearray(fake_exe([("one.upk", A)]))
        optional = PE_AT + 24
        struct.pack_into("<I", raw, optional + 112 + 2 * 8, 0)
        with self.assertRaises(core.Problem):
            core.read_list(bytes(raw))


class SwitchingOffTests(unittest.TestCase):
    def setUp(self):
        self.raw = fake_exe([("one.upk", A), ("two.upk", B), ("three.ini", C)])
        self.entries = core.read_list(self.raw)

    def test_one_entry_costs_exactly_one_byte(self):
        changed, differs = core.switch_off(self.raw, [self.entries[0]])
        self.assertEqual(1, len(differs))
        self.assertEqual(len(self.raw), len(changed))

    def test_the_name_stops_matching_and_the_others_do_not_move(self):
        changed, _ = core.switch_off(self.raw, [self.entries[0]])
        names = [e.name for e in core.read_list(changed)]
        self.assertEqual(["one.upX".lower(), "two.upk", "three.ini"], names)

    def test_the_hash_itself_is_left_alone(self):
        changed, _ = core.switch_off(self.raw, [self.entries[0]])
        self.assertEqual([A, B, C], [e.sha1 for e in core.read_list(changed)])

    def test_the_code_is_never_touched(self):
        changed, _ = core.switch_off(self.raw, self.entries)
        self.assertEqual(core.code_fingerprint(self.raw),
                         core.code_fingerprint(changed))

    def test_every_entry_at_once_costs_one_byte_each(self):
        changed, differs = core.switch_off(self.raw, self.entries)
        self.assertEqual(len(self.entries), len(differs))
        self.assertEqual([], core.switched_off(self.raw, self.raw))
        # live=changed, stock=raw: the names the stock program had and this one
        # no longer answers to
        self.assertEqual(["one.upk", "three.ini", "two.upk"],
                         core.switched_off(changed, self.raw))

    def test_doing_it_twice_does_not_put_the_name_back(self):
        once, _ = core.switch_off(self.raw, [self.entries[0]])
        twice, _ = core.switch_off(once, core.read_list(once)[:1])
        self.assertNotIn("one.upk", [e.name for e in core.read_list(twice)])

    def test_an_entry_pointing_outside_the_list_is_refused(self):
        stray = core.Entry("one.upk", 4, A)
        with self.assertRaises(core.Problem):
            core.switch_off(self.raw, [stray])

    def test_entries_are_counted_separately_from_names(self):
        # the same file listed twice is two entries but one name, which is why
        # the two counts differ on the real game
        raw = fake_exe([("same.upk", A), ("other.upk", B), ("same.upk", A)])
        entries = core.read_list(raw)
        changed, differs = core.switch_off(raw, entries)
        self.assertEqual(3, len(differs))
        self.assertEqual(3, core.switched_off_entries(changed, raw))
        self.assertEqual(["other.upk", "same.upk"], core.switched_off(changed, raw))

    def test_counting_entries_against_another_build_is_refused(self):
        short = fake_exe([("one.upk", A)])
        with self.assertRaises(core.Problem):
            core.switched_off_entries(short, self.raw)

    def test_a_program_that_has_already_been_changed_can_be_spotted_alone(self):
        self.assertEqual(0, core.already_switched_off(self.raw))
        changed, _ = core.switch_off(self.raw, [self.entries[0]])
        self.assertEqual(1, core.already_switched_off(changed))
        everything, _ = core.switch_off(self.raw, self.entries)
        self.assertEqual(3, core.already_switched_off(everything))

    def test_a_backup_from_another_build_is_recognised(self):
        other = fake_exe([("one.upk", A)], code=b"\x91" * 64)
        self.assertTrue(core.backup_suits(self.raw, self.raw))
        self.assertFalse(core.backup_suits(other, self.raw))

    def test_switching_off_does_not_change_a_backup_of_the_same_build(self):
        changed, _ = core.switch_off(self.raw, self.entries)
        self.assertTrue(core.backup_suits(self.raw, changed))


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.exe = self.root / "BrgGame-Steam.exe"
        self.exe.write_bytes(fake_exe([("one.upk", A), ("two.upk", B)]))
        self.kept = self.root / "kept"

    def tearDown(self):
        self.folder.cleanup()

    def test_the_first_one_is_taken_and_matches(self):
        backup, fresh, retired = core.take_backup(self.exe, self.kept)
        self.assertTrue(fresh)
        self.assertIsNone(retired)
        self.assertEqual(self.exe.read_bytes(), backup.read_bytes())

    def test_the_same_build_is_not_taken_twice(self):
        core.take_backup(self.exe, self.kept)
        changed, _ = core.switch_off(self.exe.read_bytes(), core.read_list(self.exe.read_bytes()))
        self.exe.write_bytes(changed)
        backup, fresh, retired = core.take_backup(self.exe, self.kept)
        self.assertFalse(fresh)
        self.assertIsNone(retired)
        self.assertEqual(0, core.already_switched_off(backup.read_bytes()),
                         "the original backup must not be replaced by a changed one")

    def test_after_a_game_update_the_old_backup_is_kept_aside_and_a_new_one_taken(self):
        first, _fresh, _retired = core.take_backup(self.exe, self.kept)
        old = first.read_bytes()
        # the game updates: same list, different code
        self.exe.write_bytes(fake_exe([("one.upk", A), ("two.upk", B)], code=b"\x91" * 64))
        backup, fresh, retired = core.take_backup(self.exe, self.kept)
        self.assertTrue(fresh, "a new build needs its own backup")
        self.assertIsNotNone(retired)
        self.assertEqual(old, retired.read_bytes(), "the old one must be kept, not lost")
        self.assertTrue(core.backup_suits(backup.read_bytes(), self.exe.read_bytes()))

    def test_two_retired_backups_do_not_collide(self):
        core.take_backup(self.exe, self.kept)
        for code in (b"\x91" * 64, b"\x92" * 64):
            self.exe.write_bytes(fake_exe([("one.upk", A), ("two.upk", B)], code=code))
            core.take_backup(self.exe, self.kept)
        kept = sorted(p.name for p in self.kept.iterdir())
        self.assertEqual(3, len(kept), kept)


class BackupCheckTests(unittest.TestCase):
    """A backup taken from an already switched-off program is the worst case:
    the status is read off it, so a stock game reads as fully switched off."""

    def setUp(self):
        self.stock = fake_exe([("one.upk", A), ("two.upk", B)])
        self.off, _ = core.switch_off(self.stock, core.read_list(self.stock))
        self.other_build = fake_exe([("one.upk", A), ("two.upk", B)], code=b"\x91" * 64)

    def test_a_stock_backup_of_the_same_build_is_usable(self):
        check = core.check_backup(self.stock, self.off)
        self.assertTrue(check.usable)
        self.assertTrue(check.stock)
        self.assertEqual("", check.problem())

    def test_a_switched_off_backup_is_not_stock(self):
        check = core.check_backup(self.off, self.stock)
        self.assertFalse(check.usable)
        self.assertFalse(check.stock)
        self.assertEqual(2, check.switched_off)
        self.assertIn("not a stock copy", check.problem())

    def test_another_build_is_refused_even_when_stock(self):
        check = core.check_backup(self.other_build, self.stock)
        self.assertTrue(check.stock)
        self.assertFalse(check.same_build)
        self.assertIn("different build", check.problem())

    def test_being_switched_off_is_said_before_the_build(self):
        # Both wrong: the one that changes what restoring DOES is the one to say.
        off_other, _ = core.switch_off(self.other_build, core.read_list(self.other_build))
        self.assertIn("not a stock copy", core.check_backup(off_other, self.stock).problem())


class ScanTests(unittest.TestCase):
    def test_it_finds_the_checked_files_that_no_longer_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sub").mkdir()
            good = root / "sub" / "one.upk"
            good.write_bytes(b"hello")
            bad = root / "two.upk"
            bad.write_bytes(b"changed")
            free = root / "free.upk"
            free.write_bytes(b"whatever")

            raw = fake_exe([
                ("one.upk", hashlib.sha1(b"hello").hexdigest()),
                ("two.upk", hashlib.sha1(b"original").hexdigest()),
            ])
            found = core.scan_modified(raw, root)
            self.assertEqual(["two.upk"], [m.name for m in found])
            self.assertEqual(hashlib.sha1(b"changed").hexdigest(), found[0].found)

    def test_it_can_be_stopped_part_way(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for i in range(5):
                (root / f"file{i}.upk").write_bytes(b"x")
            raw = fake_exe([(f"file{i}.upk", A) for i in range(5)])
            seen = []
            core.scan_modified(raw, root,
                               progress=lambda done, total, path: seen.append(done) or done < 2)
            self.assertEqual([1, 2], seen)


if __name__ == "__main__":
    unittest.main()
