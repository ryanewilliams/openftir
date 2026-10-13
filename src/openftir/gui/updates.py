"""Update checks in the GUI: a quiet check on startup, Help -> Check for
Updates..., and the download + restart (see ``openftir.updater``)."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from openftir import __version__, updater
from openftir.gui.workers import CallWorker, start_worker

_AUTO_CHECK_KEY = "updates/checkOnStartup"
_LAST_CHECK_KEY = "updates/lastCheck"
#: startup checks happen at most this often
_AUTO_CHECK_INTERVAL_S = 12 * 3600


def _settings() -> QSettings:
    return QSettings("OpenFTIR", "OpenFTIR")


class _PrepareWorker(CallWorker):
    progress = Signal(int, int)


class UpdateController(QObject):
    def __init__(self, window: QWidget, is_busy=lambda: False, has_open_spectra=lambda: False) -> None:
        super().__init__(window)
        self._window = window
        self._is_busy = is_busy
        self._has_open_spectra = has_open_spectra
        self._checking = False
        self._cancel = threading.Event()

    # -- settings ------------------------------------------------------------------------

    @staticmethod
    def auto_check_enabled() -> bool:
        return bool(_settings().value(_AUTO_CHECK_KEY, True, type=bool))

    @staticmethod
    def set_auto_check_enabled(enabled: bool) -> None:
        _settings().setValue(_AUTO_CHECK_KEY, bool(enabled))

    # -- checking ------------------------------------------------------------------------

    def check_on_startup(self) -> None:
        """A quiet background check: only says anything if there's an update."""
        if not updater.is_packaged() or not self.auto_check_enabled():
            return
        last = float(_settings().value(_LAST_CHECK_KEY, 0.0, type=float))
        if time.time() - last < _AUTO_CHECK_INTERVAL_S:
            return
        QTimer.singleShot(3000, lambda: self.check(manual=False))

    def check(self, manual: bool = True) -> None:
        if self._checking:
            return
        self._checking = True
        worker = CallWorker(updater.fetch_latest_release)
        worker.succeeded.connect(lambda release: self._on_checked(release, manual))
        worker.failed.connect(lambda message: self._on_check_failed(message, manual))
        start_worker(worker)

    def _on_check_failed(self, message: str, manual: bool) -> None:
        self._checking = False
        if manual:
            QMessageBox.warning(self._window, "Check for Updates", message)

    def _on_checked(self, release: updater.Release, manual: bool) -> None:
        self._checking = False
        _settings().setValue(_LAST_CHECK_KEY, time.time())
        if not updater.is_newer(release.version):
            if manual:
                QMessageBox.information(
                    self._window, "Check for Updates", f"You have the latest version of OpenFTIR ({__version__})."
                )
            return
        self._offer(release)

    # -- offering + installing -----------------------------------------------------------

    def _offer(self, release: updater.Release) -> None:
        notes = release.notes
        if len(notes) > 1200:
            notes = notes[:1200].rsplit("\n", 1)[0] + "\n…"
        reason = updater.why_not_self_update()
        box = QMessageBox(self._window)
        box.setWindowTitle("Update available")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>OpenFTIR {release.version}</b> is available. You have {__version__}.")
        box.setInformativeText(reason or "OpenFTIR will close, update itself and reopen.")
        if notes:
            box.setDetailedText(notes)
        install = None
        if reason is None:
            install = box.addButton("Install and restart", QMessageBox.AcceptRole)
        download = box.addButton("Download page", QMessageBox.HelpRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is download:
            QDesktopServices.openUrl(QUrl(release.page_url))
        elif install is not None and clicked is install:
            self._install(release)

    def _install(self, release: updater.Release) -> None:
        if self._is_busy():
            QMessageBox.information(
                self._window, "Update", "Finish or cancel the current collection, then install the update."
            )
            return
        if self._has_open_spectra():
            answer = QMessageBox.question(
                self._window,
                "Update",
                "OpenFTIR will close to update. Any spectra you haven't saved will be lost.\n\nUpdate now?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return

        dialog = QProgressDialog(f"Downloading OpenFTIR {release.version}…", "Cancel", 0, 100, self._window)
        dialog.setWindowTitle("Update")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)

        self._cancel.clear()
        worker = _PrepareWorker(lambda: updater.prepare_update(release, progress=worker.progress.emit, cancel=self._cancel))
        worker.progress.connect(lambda done, total: dialog.setValue(int(100 * done / total)) if total else None)
        dialog.canceled.connect(self._cancel.set)

        def ready(command) -> None:
            dialog.close()
            updater.launch_detached(command)
            QApplication.instance().quit()

        def failed(message: str) -> None:
            dialog.close()
            if not self._cancel.is_set():
                QMessageBox.warning(self._window, "Update", f"{message}\n\nYou can also download it from the Releases page.")

        worker.succeeded.connect(ready)
        worker.failed.connect(failed)
        start_worker(worker)
