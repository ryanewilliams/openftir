"""Collect dock: driver selection, acquisition settings, background/sample
collection, live single-beam preview and diagnostics.

Every call into an :class:`~openftir.instruments.base.Instrument` runs on a
worker thread (``openftir.gui.workers``) so a slow or blocking driver never
freezes the UI.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from openftir.instruments import registry
from openftir.instruments.base import CollectParams, Instrument, InstrumentInfo
from openftir.processing import ratio
from openftir.spectrum import Spectrum, YUnit
from openftir.gui.workers import CallWorker, CollectWorker, start_worker

_BACKGROUND_WARN_AFTER = timedelta(minutes=60)
_SIMULATED_SAMPLES = ["polystyrene", "water-vapour", "none"]
_FORMATS = {"Absorbance": YUnit.ABSORBANCE, "%Transmittance": YUnit.TRANSMITTANCE}


class CollectPanel(QWidget):
    #: a finished, background-ratioed sample spectrum plus its chosen name
    spectrum_collected = Signal(object, str)
    #: a raw single-beam result to show as a live preview (spectrum, label)
    preview_ready = Signal(object, str)
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instrument: Instrument | None = None
        self._info: InstrumentInfo | None = None
        self._background: Spectrum | None = None
        self._background_time: datetime | None = None
        self._worker = None  # current CollectWorker, so Cancel can reach it
        self._instrument_lock = threading.Lock()
        self._pending_mode: str | None = None

        layout = QVBoxLayout(self)

        # -- driver selection -------------------------------------------------
        driver_box = QGroupBox("Instrument")
        driver_form = QFormLayout(driver_box)
        self.driver_combo = QComboBox()
        self._refresh_drivers()
        driver_form.addRow("Driver:", self.driver_combo)

        self.sim_sample_combo = QComboBox()
        self.sim_sample_combo.addItems(_SIMULATED_SAMPLES)
        self.sim_sample_combo.currentTextChanged.connect(self._on_sim_sample_changed)
        driver_form.addRow("Simulated sample:", self.sim_sample_combo)

        conn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.disconnect_btn.setEnabled(False)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.disconnect_btn)
        driver_form.addRow(conn_row)

        self.status_label = QLabel("Not connected")
        driver_form.addRow(self.status_label)

        self.diagnostics_btn = QPushButton("Diagnostics...")
        self.diagnostics_btn.clicked.connect(self._on_diagnostics)
        self.diagnostics_btn.setEnabled(False)
        driver_form.addRow(self.diagnostics_btn)
        layout.addWidget(driver_box)

        # -- acquisition settings --------------------------------------------
        settings_box = QGroupBox("Settings")
        settings_form = QFormLayout(settings_box)
        self.scans_spin = QSpinBox()
        self.scans_spin.setRange(1, 10000)
        self.scans_spin.setValue(16)
        settings_form.addRow("Scans:", self.scans_spin)

        self.resolution_combo = QComboBox()
        self._set_resolutions((0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0), default=4.0)
        settings_form.addRow("Resolution (cm⁻¹):", self.resolution_combo)

        range_row = QHBoxLayout()
        self.x_min_spin = QDoubleSpinBox()
        self.x_min_spin.setRange(0, 20000)
        self.x_min_spin.setValue(CollectParams.x_min)
        self.x_max_spin = QDoubleSpinBox()
        self.x_max_spin.setRange(0, 20000)
        self.x_max_spin.setValue(CollectParams.x_max)
        range_row.addWidget(self.x_min_spin)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.x_max_spin)
        settings_form.addRow("Range (cm⁻¹):", range_row)

        self.apodization_combo = QComboBox()
        self.apodization_combo.addItems(["happ-genzel", "triangular", "boxcar", "blackman-harris"])
        settings_form.addRow("Apodization:", self.apodization_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(list(_FORMATS))
        settings_form.addRow("Final format:", self.format_combo)
        layout.addWidget(settings_box)

        # -- background age ----------------------------------------------------
        self.bg_age_label = QLabel("No background collected")
        layout.addWidget(self.bg_age_label)
        self._age_timer = QTimer(self)
        self._age_timer.setInterval(10_000)
        self._age_timer.timeout.connect(self._update_background_age)
        self._age_timer.start()

        # -- collect buttons ----------------------------------------------------
        collect_row = QHBoxLayout()
        self.bg_btn = QPushButton("Collect Background")
        self.bg_btn.clicked.connect(lambda: self._start_collect("background"))
        self.sample_btn = QPushButton("Collect Sample")
        self.sample_btn.clicked.connect(lambda: self._start_collect("sample"))
        self.bg_btn.setEnabled(False)
        self.sample_btn.setEnabled(False)
        collect_row.addWidget(self.bg_btn)
        collect_row.addWidget(self.sample_btn)
        layout.addLayout(collect_row)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.cancel_btn)
        layout.addLayout(progress_row)

        layout.addStretch(1)
        self._on_driver_changed()
        self.driver_combo.currentTextChanged.connect(self._on_driver_changed)

    # -- driver management ------------------------------------------------------------

    def _refresh_drivers(self) -> None:
        self.driver_combo.clear()
        for driver_id in sorted(registry.available_drivers()):
            self.driver_combo.addItem(driver_id)

    def _on_driver_changed(self, *_args) -> None:
        is_simulated = self.driver_combo.currentText() == "simulated"
        self.sim_sample_combo.setVisible(is_simulated)

    def _set_resolutions(self, resolutions: tuple[float, ...], default: float) -> None:
        self.resolution_combo.clear()
        for r in resolutions:
            self.resolution_combo.addItem(f"{r:g}", r)
        idx = self.resolution_combo.findData(default)
        if idx >= 0:
            self.resolution_combo.setCurrentIndex(idx)

    def _on_sim_sample_changed(self, name: str) -> None:
        if self._instrument is not None and hasattr(self._instrument, "set_sample"):
            worker = CallWorker(self._locked(self._instrument.set_sample), name)
            worker.failed.connect(lambda msg: self._report_error("Set Sample", msg))
            start_worker(worker)

    def _on_connect(self) -> None:
        driver_id = self.driver_combo.currentText()
        if not driver_id:
            QMessageBox.warning(self, "Connect", "No instrument driver available.")
            return
        try:
            if driver_id == "simulated":
                try:
                    instrument = registry.create(driver_id, sample=self.sim_sample_combo.currentText())
                except TypeError:
                    instrument = registry.create(driver_id)
            else:
                instrument = registry.create(driver_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Connect", f"Could not create driver:\n{exc}")
            return

        self._instrument = instrument
        self.connect_btn.setEnabled(False)
        self.status_label.setText("Connecting...")
        worker = CallWorker(self._locked(instrument.connect))
        worker.succeeded.connect(self._on_connected)
        worker.failed.connect(self._on_connect_failed)
        start_worker(worker)

    def _on_connected(self, info: InstrumentInfo) -> None:
        self._info = info
        self.status_label.setText(f"Connected: {info.vendor} {info.model}")
        self.disconnect_btn.setEnabled(True)
        self.diagnostics_btn.setEnabled(True)
        self.bg_btn.setEnabled(True)
        self.sample_btn.setEnabled(True)
        if info.resolutions:
            current = self.resolution_combo.currentData() or 4.0
            self._set_resolutions(info.resolutions, default=current if current in info.resolutions else info.resolutions[0])
        self.status_message.emit(f"Connected to {info.vendor} {info.model}")

    def _on_connect_failed(self, message: str) -> None:
        self._instrument = None
        self.connect_btn.setEnabled(True)
        self.status_label.setText("Not connected")
        self._report_error("Connect", message)

    def _on_disconnect(self) -> None:
        if self._instrument is None:
            return
        worker = CallWorker(self._locked(self._instrument.disconnect))
        worker.succeeded.connect(lambda *_: self._on_disconnected())
        worker.failed.connect(lambda msg: (self._on_disconnected(), self._report_error("Disconnect", msg)))
        start_worker(worker)

    def _on_disconnected(self) -> None:
        self._instrument = None
        self._info = None
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.diagnostics_btn.setEnabled(False)
        self.bg_btn.setEnabled(False)
        self.sample_btn.setEnabled(False)
        self.status_label.setText("Not connected")
        self.status_message.emit("Disconnected")

    def _on_diagnostics(self) -> None:
        if self._instrument is None:
            return
        worker = CallWorker(self._locked(self._instrument.diagnostics))
        worker.succeeded.connect(self._show_diagnostics)
        worker.failed.connect(lambda msg: self._report_error("Diagnostics", msg))
        start_worker(worker)

    def _show_diagnostics(self, report) -> None:
        lines = [f"Overall: {'OK' if report.ok else 'PROBLEM'}", ""]
        lines += [f"{name}: {status}" for name, status in report.items.items()]
        QMessageBox.information(self, "Diagnostics", "\n".join(lines))

    # -- collection ------------------------------------------------------------------

    def _build_params(self) -> CollectParams:
        return CollectParams(
            scans=self.scans_spin.value(),
            resolution=self.resolution_combo.currentData() or 4.0,
            x_min=self.x_min_spin.value(),
            x_max=self.x_max_spin.value(),
            apodization=self.apodization_combo.currentText(),
        )

    def _start_collect(self, mode: str) -> None:
        if self._instrument is None:
            return
        self._pending_mode = mode
        params = self._build_params()
        self.progress_bar.setRange(0, params.scans)
        self.progress_bar.setValue(0)
        self._set_busy(True)

        worker = CollectWorker(self._instrument, params, lock=self._instrument_lock)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_collect_succeeded)
        worker.cancelled.connect(self._on_collect_cancelled)
        worker.failed.connect(self._on_collect_failed)
        start_worker(worker)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)

    def _on_collect_succeeded(self, spectrum: Spectrum) -> None:
        self._set_busy(False)
        mode = self._pending_mode
        label = "Background (single beam)" if mode == "background" else "Sample (single beam)"
        self.preview_ready.emit(spectrum, label)

        if mode == "background":
            self._background = spectrum
            self._background_time = datetime.now()
            self._update_background_age()
            self.status_message.emit("Background collected")
            return

        if self._background is None:
            QMessageBox.warning(self, "Collect Sample", "Collect a background first.")
            return

        to_unit = _FORMATS[self.format_combo.currentText()]
        try:
            result = ratio(spectrum, self._background, to=to_unit)
        except Exception as exc:  # noqa: BLE001
            self._report_error("Collect Sample", str(exc))
            return

        name, ok = QInputDialog.getText(self, "Name Spectrum", "Name for the new spectrum:", text="Sample")
        if not ok:
            name = "Sample"
        self.spectrum_collected.emit(result, name or "Sample")
        self.status_message.emit(f"Collected and ratioed '{name}'")

    def _on_collect_cancelled(self) -> None:
        self._set_busy(False)
        self.status_message.emit("Collection cancelled")

    def _on_collect_failed(self, message: str) -> None:
        self._set_busy(False)
        self._report_error("Collect", message)

    def _on_cancel(self) -> None:
        if isinstance(self._worker, CollectWorker):
            self._worker.cancel()

    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.setEnabled(busy)
        self.bg_btn.setEnabled(not busy and self._instrument is not None)
        self.sample_btn.setEnabled(not busy and self._instrument is not None)
        if not busy:
            self.progress_bar.setValue(0)

    def _update_background_age(self) -> None:
        if self._background_time is None:
            self.bg_age_label.setText("No background collected")
            self.bg_age_label.setStyleSheet("")
            return
        age = datetime.now() - self._background_time
        minutes = int(age.total_seconds() // 60)
        self.bg_age_label.setText(f"Background age: {minutes} min")
        if age > _BACKGROUND_WARN_AFTER:
            self.bg_age_label.setStyleSheet("color: #d62728; font-weight: bold;")
        else:
            self.bg_age_label.setStyleSheet("")

    # -- helpers ------------------------------------------------------------------------

    def _locked(self, fn):
        """Wrap an instrument call so only one runs at a time.

        Drivers are not required to be thread-safe, and workers for e.g.
        set_sample and a collection could otherwise overlap.
        """
        def call(*args, **kwargs):
            with self._instrument_lock:
                return fn(*args, **kwargs)
        return call

    def _report_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
