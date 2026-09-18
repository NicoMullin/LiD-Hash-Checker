[#VirusTotal](https://www.virustotal.com/gui/url/a282a3effdf0991efd97cc76169b2ddb9f84acdd21ce92d2d2e6d51b4cedae3a)

# LET IT DIE - file check

A small Windows tool that switches off the game's own file verification, so
mods that replace a checked package load without anyone editing hashes by hand.

It changes **one byte per entry in a list of file names**, inside the game's
executable. It never changes the game's code, never changes a hash value, and
never changes the executable's size - and it proves all three before it writes
anything.

## What it is actually doing

`BrgGame-Steam.exe` carries a list of file names, each with the SHA-1 the game
expects that file to have. The list lives in a resource inside the executable
and, on current builds, names 8,266 files - 7,678 `.upk` packages, 221 `.ini`
and 139 `.usf`.

When the game loads a file it looks the name up in that list. Found means the
file is checked, and a file that no longer matches is refused - you get an error
box naming the package before the game even reaches its intro. **Not found means
the file is never checked at all.** That is why a great many mods have always
worked untouched: 251 files on disk are not in the list.

So this tool does not disable anything in code. It changes the last character of
an entry's name, `...upk` to `...upX`, so the lookup misses and the game carries
on as it already does for any unlisted file. The hash sits there untouched,
under a name nothing asks for.

The list is found through the executable's own resource directory, never a fixed
offset, so this keeps working after a game update without anything being redone.

## Using it

Close the game first.

**The window** - run `LiD File Check.exe`, or `python app.py`:

| | |
|---|---|
| **Switch the check off** | every entry, with a confirmation naming the byte count |
| **One file...** | just that file; tells you if it was never checked anyway |
| **Put the original back** | restores the backup |
| **What is relying on this?** | lists every checked file on disk that no longer matches, i.e. what would break if the check came back |

**The command line** - `lid_hash_test.py`, if you would rather not run a window:

```
python lid_hash_test.py status
python lid_hash_test.py check UI_ButtonGuide_STM_SF.upk
python lid_hash_test.py off UI_ButtonGuide_STM_SF.upk
python lid_hash_test.py off --all
python lid_hash_test.py restore
```

Needs Python 3.8+ and nothing installed. The window needs PySide6.

## Getting back to normal

Two ways, and they do different amounts:

- **Put the original back** - restores the executable only.
- **Steam - Verify integrity of game files** - restores the executable *and*
  every modified game file, including `masters.db`.

The second one matters more than it sounds. Once you have modded files installed,
they are relying on the check being off. Restore the executable on its own and
the game starts refusing them, one error box at a time. Use
**What is relying on this?** first - it tells you exactly which files those are.

## When the game updates

Steam replaces the executable, so the check comes back on and your backup is
suddenly from an older build. The tool handles this: it keeps the old backup
aside rather than deleting it, takes a fresh one from the new build, and refuses
to restore a backup that does not match what is installed. Restoring a previous
build's executable over newer game files would be worse than the check being off.

You will want to switch it off again after every update, and reinstall your mods
if the update replaced the packages they change.

**Worth knowing:** with the check off, a mod built for an older build loads
without complaint. If a game update changed a package that a mod replaces, the
mod quietly puts the old version of that content back. Nothing will warn you.



## Building it

```
pip install PySide6 pyinstaller
python build.py                 # dist/LiD File Check/
python build.py --onefile       # a single .exe
```

The build runs the tests first and refuses to package a red one. Hand out the
whole folder rather than just the `.exe` - it keeps its backup beside itself.

## Tests

```
python -m unittest discover -s tests -t .
```

25 tests, and they build a miniature Windows program of their own rather than
needing a copy of the game, so they run anywhere.

## Layout

```
app.py              the window
hashtool/core.py    reading the list, changing it, backups, the scan
lid_hash_test.py    the same job from a command line, no PySide6 needed
tests/              tests for core
build.py            packaging
```

All the logic is in `core.py` and none of it knows the interface exists.

## Licence

MIT - see LICENSE.

- KSFA
