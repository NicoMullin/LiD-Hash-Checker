"""LET IT DIE - file check tool.

A small window over hashtool.core. It shows what the game's executable expects,
switches the file check off for one file or all of them, puts the original back,
and can tell you which of your game files are relying on the check being off.

Nothing happens without a click, and every change says exactly what it did.

- KSFA
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hashtool import core

APP_NAME = "LET IT DIE - file check"


def beside_the_program() -> Path:
    """Where this is running from, as a person would point at it.

    Frozen by PyInstaller, ``__file__`` is inside a temporary folder that is
    deleted on exit - so the backup of someone's game executable must not go
    anywhere near it. It belongs beside the program they double-clicked.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BACKUP_FOLDER = beside_the_program() / "backup"


class ScanThread(QThread):
    """Hashing the game folder, off the interface's thread."""

    progress = Signal(int, int, str)
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, stock: bytes, root: Path):
        super().__init__()
        self._stock = stock
        self._root = root
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        def step(done: int, total: int, path: Path) -> bool:
            self.progress.emit(done, total, path.name)
            return not self._stop
        try:
            self.done.emit(core.scan_modified(self._stock, self._root, step))
        except Exception as problem:            # noqa: BLE001 - shown to the user
            self.failed.emit(str(problem))


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(760, 560)
        self.exe: Path | None = None
        self.scan: ScanThread | None = None

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.where = QLabel("looking for the game...")
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        browse = QPushButton("Choose folder...")
        browse.clicked.connect(self.choose_game)
        top = QHBoxLayout()
        top.addWidget(self.where, 1)
        top.addWidget(browse)
        layout.addLayout(top)

        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}
        for row, caption in enumerate(("Game build", "File list", "Switched off",
                                       "Backup")):
            label = QLabel(caption)
            label.setStyleSheet("color: palette(mid);")
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.values[caption] = value
        layout.addWidget(box)

        buttons = QHBoxLayout()
        self.off_all = QPushButton("Switch the check off")
        self.off_all.clicked.connect(self.do_switch_off_everything)
        self.off_one = QPushButton("One file...")
        self.off_one.clicked.connect(self.do_switch_off_one)
        self.restore = QPushButton("Put the original back")
        self.restore.clicked.connect(self.do_restore)
        self.scan_button = QPushButton("What is relying on this?")
        self.scan_button.clicked.connect(self.do_scan)
        for button in (self.off_all, self.off_one, self.restore, self.scan_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self.setCentralWidget(central)
        self.find_game()

    # -- plumbing ----------------------------------------------------------

    def say(self, line: str = "") -> None:
        self.log.appendPlainText(line)

    def complain(self, problem: str) -> None:
        QMessageBox.warning(self, APP_NAME, problem)
        self.say("stopped: " + problem)

    def live(self) -> bytes:
        if self.exe is None:
            raise core.Problem("no game chosen yet")
        return self.exe.read_bytes()

    def backup(self) -> Path:
        return core.backup_for(self.exe, BACKUP_FOLDER)      # type: ignore[arg-type]

    def find_game(self, given: str | None = None) -> None:
        try:
            self.exe = core.find_game(given)
        except core.Problem as problem:
            self.exe = None
            self.where.setText(str(problem))
            self.say("stopped: " + str(problem))
        else:
            self.where.setText(str(self.exe))
            self.say("game: " + str(self.exe))
        self.refresh()

    def choose_game(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Where LET IT DIE is installed")
        if folder:
            self.find_game(folder)

    def refresh(self) -> None:
        for button in (self.off_all, self.off_one, self.restore, self.scan_button):
            button.setEnabled(self.exe is not None)
        if self.exe is None:
            for value in self.values.values():
                value.setText("-")
            return
        try:
            raw = self.live()
            entries = core.read_list(raw)
            self.values["Game build"].setText(core.code_fingerprint(raw)[:16] + "...")
            names = len({e.name for e in entries})
            self.values["File list"].setText(
                f"{len(entries):,} entries, {names:,} distinct file names"
                + (f"  ({len(entries) - names:,} listed twice)"
                   if len(entries) != names else ""))

            backup = self.backup()
            if backup.exists():
                stock = backup.read_bytes()
                same_build = core.backup_suits(stock, raw)
                changed = core.switched_off_entries(raw, stock)
                lost = len(core.switched_off(raw, stock))
                self.values["Switched off"].setText(
                    f"{changed:,} of {len(entries):,} entries"
                    + (f"  -  {lost:,} file names the game can no longer look up"
                       if changed else ""))
                self.values["Backup"].setText(
                    str(backup) + ("" if same_build else
                                   "   - FROM A DIFFERENT GAME BUILD, do not restore it"))
            else:
                looks = core.already_switched_off(raw)
                self.values["Switched off"].setText(
                    f"{looks:,} (going by the names; no backup to compare against)")
                self.values["Backup"].setText("none yet")
            self.restore.setEnabled(backup.exists())
        except core.Problem as problem:
            self.complain(str(problem))

    # -- the things it can do ---------------------------------------------

    def ready_to_write(self) -> bool:
        if core.game_is_running():
            self.complain("the game is running - close it first")
            return False
        return True

    def ensure_backup(self) -> Path | None:
        """A copy to come back to, or None if we should not go on."""
        backup = self.backup()
        raw = self.live()
        if backup.exists() and core.backup_suits(backup.read_bytes(), raw):
            return backup
        if backup.exists():
            # From an older build: the game has updated since. Keep it, but it
            # is not the way back from where we are now.
            retired = core.retire_backup(backup)
            self.say(f"the backup was from an older build of the game - kept as "
                     f"{retired.name}, and a fresh one will be taken")
        already = core.already_switched_off(raw)
        if already:
            answer = QMessageBox.question(
                self, APP_NAME,
                f"The installed game already has {already:,} entries switched off, "
                "so a backup taken now would not be the original.\n\n"
                "Point me at a clean copy of the executable instead?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                chosen, _ = QFileDialog.getOpenFileName(
                    self, "A clean copy of " + core.EXE_NAME, "", "Programs (*.exe*)")
                if not chosen:
                    return None
                data = Path(chosen).read_bytes()
                if core.already_switched_off(data):
                    self.complain("that copy has been switched off too")
                    return None
                if not core.backup_suits(data, raw):
                    self.complain("that copy is from a different build of the game")
                    return None
                BACKUP_FOLDER.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(data)
                self.say(f"backup taken from {chosen}")
                return backup
            self.say("carrying on without a clean backup - Steam's Verify integrity "
                     "of game files is then the only way back")
        backup, fresh, retired = core.take_backup(self.exe, BACKUP_FOLDER)  # type: ignore[arg-type]
        if retired is not None:
            self.say(f"older build's backup kept as {retired.name}")
        if fresh:
            self.say(f"backup: {backup}")
        return backup

    def apply(self, targets, description: str) -> None:
        if not self.ready_to_write():
            return
        try:
            raw = self.live()
            if not targets:
                self.say("nothing to do - " + description)
                return
            answer = QMessageBox.question(
                self, APP_NAME,
                f"Switch the game's file check off for {description}?\n\n"
                f"That changes {len(targets):,} byte(s) inside the executable's "
                "file list. No code is touched and the file stays the same size.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            if self.ensure_backup() is None:
                return
            changed, differs = core.switch_off(raw, targets)
            core.write_executable(self.exe, changed)          # type: ignore[arg-type]
            self.say(f"switched off: {description}")
            self.say(f"  {len(differs):,} byte(s) changed, all inside the file list")
            self.say(f"  code unchanged: {core.code_fingerprint(changed)}")
            self.say(f"  size unchanged: {len(changed):,} bytes")
        except core.Problem as problem:
            self.complain(str(problem))
        except OSError as problem:
            self.complain(f"could not write the executable: {problem}")
        self.refresh()

    def do_switch_off_everything(self) -> None:
        try:
            entries = core.read_list(self.live())
        except core.Problem as problem:
            self.complain(str(problem))
            return
        self.apply(entries, f"every file in the list ({len(entries):,})")

    def do_switch_off_one(self) -> None:
        name, chose = QInputDialog.getText(
            self, APP_NAME, "Which file? e.g. UI_ButtonGuide_STM_SF.upk")
        if not chose or not name.strip():
            return
        wanted = Path(name.strip()).name.lower()
        try:
            targets = [e for e in core.read_list(self.live()) if e.name == wanted]
        except core.Problem as problem:
            self.complain(str(problem))
            return
        if not targets:
            QMessageBox.information(
                self, APP_NAME,
                f"{wanted} is not in the list, so the game never checks it.\n\n"
                "You can replace that file as it is.")
            return
        self.apply(targets, wanted)

    def do_restore(self) -> None:
        if not self.ready_to_write():
            return
        backup = self.backup()
        if not backup.exists():
            self.complain("no backup here. Use Steam's Verify integrity of game files")
            return
        try:
            data = backup.read_bytes()
            if not core.backup_suits(data, self.live()):
                QMessageBox.warning(
                    self, APP_NAME,
                    "This backup is from a DIFFERENT build of the game, so it is "
                    "not the way back from the one you have installed.\n\n"
                    "Putting it back would leave you running an executable older "
                    "than your game files, which is worse than the check being "
                    "off.\n\nUse Steam's Verify integrity of game files instead.")
                self.say("stopped: that backup is from another build of the game")
                return
            core.write_executable(self.exe, data)              # type: ignore[arg-type]
            self.say(f"restored from {backup}")
        except (core.Problem, OSError) as problem:
            self.complain(str(problem))
        self.refresh()

    # -- the scan ----------------------------------------------------------

    def do_scan(self) -> None:
        backup = self.backup()
        if not backup.exists():
            self.complain("this needs the original executable to compare against, "
                          "and no backup has been taken yet")
            return
        if self.scan is not None:
            self.scan.stop()
            return
        root = core.game_root(self.exe)                        # type: ignore[arg-type]
        self.say(f"scanning {root} - this reads every checked file, so give it a while")
        self.bar.setValue(0)
        self.bar.show()
        self.scan_button.setText("Stop")
        self.scan = ScanThread(backup.read_bytes(), root)
        self.scan.progress.connect(self.scan_step)
        self.scan.done.connect(self.scan_done)
        self.scan.failed.connect(self.scan_failed)
        self.scan.start()

    def scan_step(self, done: int, total: int, name: str) -> None:
        self.bar.setMaximum(total)
        self.bar.setValue(done)
        self.bar.setFormat(f"%v of %m   {name}")

    def scan_finished(self) -> None:
        self.bar.hide()
        self.scan_button.setText("What is relying on this?")
        self.scan = None

    def scan_done(self, found: list) -> None:
        self.scan_finished()
        if not found:
            self.say("every checked file matches the original. The original "
                     "executable can go back with nothing else to undo.")
        else:
            self.say(f"{len(found)} checked file(s) no longer match, and would be "
                     "refused the moment the check came back:")
            root = core.game_root(self.exe)                    # type: ignore[arg-type]
            for item in found:
                try:
                    where = item.path.relative_to(root)
                except ValueError:
                    where = item.path
                self.say(f"  {where}")
                self.say(f"      expected {item.expected}")
                self.say(f"      found    {item.found}")
            self.say("Put those files back before restoring the original executable, "
                     "or use Steam's Verify integrity of game files, which does both.")
        self.say()

    def scan_failed(self, problem: str) -> None:
        self.scan_finished()
        self.complain(problem)

    def closeEvent(self, event) -> None:
        if self.scan is not None:
            self.scan.stop()
            self.scan.wait(5000)
        super().closeEvent(event)


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    window = Window()
    window.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
