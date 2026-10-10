"""Collect dock (Advanced mode): driver selection, acquisition settings,
background/sample collection, live single-beam preview and diagnostics.

All the actual instrument/collection state lives in
:class:`~openftir.gui.collect_controller.CollectController`; this widget is a
thin view over it, built the same way it always has been (so its own tests,
and the user-visible Advanced-mode behaviour, are unchanged). Simple mode has
its own, much smaller, view over the same controller -- see
``openftir.gui.simple_panels``.
"""

from __future__ import annotations

from datetime import datetime

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
from PySide6.QtCore import QTimer, Signal

from openftir.gui import file_dialogs
from openftir.gui.collect_controller import (
    BACKGROUND_WARN_AFTER,
    CollectController,
    DEFAULT_SCANS,
    FORMATS,
    SIMULATED_SAMPLES,
)


class CollectPanel(QWidget):
    #: a finished, background-ratioed sample spectrum plus its chosen name
    #: (also used for a raw background single beam -- see _on_collect_finished)
    spectrum_collected = Signal(object, str)
    #: a raw single-beam result to show as a live preview (spectrum, label)
    preview_ready = Signal(object, str)
    status_message = Signal(str)

    def __init__(self, controller: CollectController | None = None, parent=None):
        super().__init__(parent)
        self.controller = controller if controller is not None else CollectController(self)
        self._active = True  # see set_active()

        layout = QVBoxLayout(self)

        # -- driver selection -------------------------------------------------
        driver_box = QGroupBox("Instrument")
        self.driver_form = QFormLayout(driver_box)
        self.driver_combo = QComboBox()
        for driver_id in self.controller.available_drivers():
            self.driver_combo.addItem(driver_id)
        self.driver_form.addRow("Driver:", self.driver_combo)

        self.sim_sample_combo = QComboBox()
        self.sim_sample_combo.addItems(SIMULATED_SAMPLES)
        self.sim_sample_combo.currentTextChanged.connect(self._on_sim_sample_changed)
        self.driver_form.addRow("Simulated sample:", self.sim_sample_combo)

        conn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(lambda: self.controller.disconnect_instrument())
        self.disconnect_btn.setEnabled(False)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.disconnect_btn)
        self.driver_form.addRow(conn_row)

        self.status_label = QLabel("Not connected")
        self.driver_form.addRow(self.status_label)

        self.diagnostics_btn = QPushButton("Diagnostics...")
        self.diagnostics_btn.clicked.connect(lambda: self.controller.request_diagnostics())
        self.diagnostics_btn.setEnabled(False)
        self.driver_form.addRow(self.diagnostics_btn)
        layout.addWidget(driver_box)

        # -- acquisition settings --------------------------------------------
        settings_box = QGroupBox("Settings")
        settings_form = QFormLayout(settings_box)
        self.scans_spin = QSpinBox()
        self.scans_spin.setRange(1, 10000)
        self.scans_spin.setValue(DEFAULT_SCANS)
        self.scans_spin.valueChanged.connect(lambda v: self.controller.update_params(scans=v))
        settings_form.addRow("Scans:", self.scans_spin)

        self.resolution_combo = QComboBox()
        self._set_resolutions((0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0), default=4.0)
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_combo_changed)
        settings_form.addRow("Resolution (cm⁻¹):", self.resolution_combo)

        range_row = QHBoxLayout()
        self.x_min_spin = QDoubleSpinBox()
        self.x_min_spin.setRange(0, 20000)
        self.x_min_spin.setValue(self.controller.x_min)
        self.x_min_spin.valueChanged.connect(lambda v: self.controller.update_params(x_min=v))
        self.x_max_spin = QDoubleSpinBox()
        self.x_max_spin.setRange(0, 20000)
        self.x_max_spin.setValue(self.controller.x_max)
        self.x_max_spin.valueChanged.connect(lambda v: self.controller.update_params(x_max=v))
        range_row.addWidget(self.x_min_spin)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.x_max_spin)
        settings_form.addRow("Range (cm⁻¹):", range_row)

        self.apodization_combo = QComboBox()
        self.apodization_combo.addItems(["happ-genzel", "triangular", "boxcar", "blackman-harris"])
        self.apodization_combo.currentTextChanged.connect(lambda t: self.controller.update_params(apodization=t))
        settings_form.addRow("Apodization:", self.apodization_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(list(FORMATS))
        self.format_combo.currentTextChanged.connect(lambda t: self.controller.update_params(final_format=t))
        settings_form.addRow("Final format:", self.format_combo)
        layout.addWidget(settings_box)

        # -- background age ----------------------------------------------------
        bg_row = QHBoxLayout()
        self.bg_age_label = QLabel("No background collected")
        bg_row.addWidget(self.bg_age_label, 1)
        self.save_bg_btn = QPushButton("Save background...")
        self.save_bg_btn.clicked.connect(self._on_save_background)
        self.load_bg_btn = QPushButton("Load background...")
        self.load_bg_btn.clicked.connect(self._on_load_background)
        bg_row.addWidget(self.save_bg_btn)
        bg_row.addWidget(self.load_bg_btn)
        layout.addLayout(bg_row)
        self._update_background_ui()

        self._age_timer = QTimer(self)
        self._age_timer.setInterval(10_000)
        self._age_timer.timeout.connect(self._update_background_age_label)
        self._age_timer.start()

        # -- collect buttons ----------------------------------------------------
        collect_row = QHBoxLayout()
        self.bg_btn = QPushButton("Collect Background")
        self.bg_btn.clicked.connect(lambda: self.controller.start_collect("background"))
        self.sample_btn = QPushButton("Collect Sample")
        self.sample_btn.clicked.connect(lambda: self.controller.start_collect("sample"))
        self.bg_btn.setEnabled(False)
        self.sample_btn.setEnabled(False)
        collect_row.addWidget(self.bg_btn)
        collect_row.addWidget(self.sample_btn)
        layout.addLayout(collect_row)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(lambda: self.controller.cancel())
        self.cancel_btn.setEnabled(False)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.cancel_btn)
        layout.addLayout(progress_row)

        layout.addStretch(1)

        # -- wire up to the controller ------------------------------------------
        self.controller.connection_changed.connect(self._on_connection_changed)
        self.controller.settings_changed.connect(self._on_settings_changed)
        self.controller.progress.connect(self._on_progress)
        self.controller.collect_started.connect(self._on_collect_started)
        self.controller.collect_finished.connect(self._on_collect_finished)
        self.controller.collect_cancelled.connect(self._on_collect_cancelled)
        self.controller.collect_failed.connect(self._on_collect_failed)
        self.controller.preview_ready.connect(self._on_preview_ready)
        self.controller.background_changed.connect(self._update_background_ui)
        self.controller.diagnostics_ready.connect(self._show_diagnostics)
        self.controller.error.connect(self._on_error)
        self.controller.status_message.connect(self._on_status_message)

        idx = self.driver_combo.findText(self.controller.driver_id)
        if idx >= 0:
            self.driver_combo.setCurrentIndex(idx)
        self.driver_combo.currentTextChanged.connect(self._on_driver_combo_changed)
        self._on_driver_combo_changed(self.driver_combo.currentText())
        self._on_settings_changed()
        self._on_connection_changed(self.controller.connected, self.controller.info)

    # -- test-compatibility passthroughs -------------------------------------------------

    @property
    def _instrument(self):
        return self.controller._instrument

    @property
    def _background(self):
        return self.controller.background

    # -- active/inactive (Simple <-> Advanced mode switch) --------------------------------

    def set_active(self, active: bool) -> None:
        """Only the mode currently on screen should react to the shared
        controller -- otherwise a collection started from Simple mode would
        also pop Advanced mode's "name the spectrum" dialog, invisibly."""
        self._active = active
        if active:
            self._on_connection_changed(self.controller.connected, self.controller.info)
            self._on_settings_changed()
            self._update_background_ui()
            self._set_busy(self.controller.busy)

    # -- driver management ------------------------------------------------------------

    def _on_driver_combo_changed(self, driver_id: str) -> None:
        is_simulated = driver_id == "simulated"
        self.sim_sample_combo.setVisible(is_simulated)
        # Hide both the label and the combo (not just the combo) when the
        # connected driver isn't "simulated" -- a dangling "Simulated
        # sample:" label with no visible control next to it looked broken.
        self.driver_form.setRowVisible(self.sim_sample_combo, is_simulated)
        if driver_id:
            self.controller.set_driver(driver_id)

    def _set_resolutions(self, resolutions: tuple[float, ...], default: float) -> None:
        self.resolution_combo.blockSignals(True)
        self.resolution_combo.clear()
        for r in resolutions:
            self.resolution_combo.addItem(f"{r:g}", r)
        idx = self.resolution_combo.findData(default)
        if idx >= 0:
            self.resolution_combo.setCurrentIndex(idx)
        self.resolution_combo.blockSignals(False)

    def _on_resolution_combo_changed(self, _index: int) -> None:
        value = self.resolution_combo.currentData()
        if value is not None:
            self.controller.update_params(resolution=float(value))

    def _on_sim_sample_changed(self, name: str) -> None:
        if not self._active:
            return
        self.controller.set_sim_sample(name)

    def _on_connect_clicked(self) -> None:
        if not self.controller.driver_id:
            QMessageBox.warning(self, "Connect", "No instrument driver available.")
            return
        self.connect_btn.setEnabled(False)
        self.status_label.setText("Connecting...")
        self.controller.connect_instrument()

    def _on_connection_changed(self, connected: bool, info) -> None:
        if not self._active:
            return
        self.disconnect_btn.setEnabled(connected)
        self.diagnostics_btn.setEnabled(connected)
        busy = self.controller.busy
        self.bg_btn.setEnabled(connected and not busy)
        self.sample_btn.setEnabled(connected and not busy)
        self.connect_btn.setEnabled(not connected)
        if connected and info is not None:
            self.status_label.setText(f"Connected: {info.vendor} {info.model}")
        else:
            self.status_label.setText("Not connected")

    def _show_diagnostics(self, report) -> None:
        if not self._active:
            return
        lines = [f"Overall: {'OK' if report.ok else 'PROBLEM'}", ""]
        lines += [f"{name}: {status}" for name, status in report.items.items()]
        QMessageBox.information(self, "Diagnostics", "\n".join(lines))

    # -- settings refresh ---------------------------------------------------------------

    def _on_settings_changed(self) -> None:
        if not self._active:
            return
        c = self.controller
        widgets = (
            self.scans_spin,
            self.resolution_combo,
            self.x_min_spin,
            self.x_max_spin,
            self.apodization_combo,
            self.format_combo,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self.scans_spin.setValue(c.scans)
            self._set_resolutions(c.resolutions, c.resolution)
            self.x_min_spin.setValue(c.x_min)
            self.x_max_spin.setValue(c.x_max)
            idx = self.apodization_combo.findText(c.apodization)
            if idx >= 0:
                self.apodization_combo.setCurrentIndex(idx)
            idx = self.format_combo.findText(c.final_format)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
        finally:
            for w in widgets:
                w.blockSignals(False)

    # -- collection -----------------------------------------------------------------------

    def _on_progress(self, done: int, total: int) -> None:
        if not self._active:
            return
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)

    def _on_collect_started(self, _mode: str) -> None:
        if not self._active:
            return
        self.progress_bar.setRange(0, max(self.controller.scans, 1))
        self.progress_bar.setValue(0)
        self._set_busy(True)

    def _on_collect_finished(self, mode: str, spectrum, suggested_name: str) -> None:
        if not self._active:
            return
        self._set_busy(False)
        if mode == "sample":
            name, ok = QInputDialog.getText(self, "Name Spectrum", "Name for the new spectrum:", text=suggested_name)
            if not ok:
                name = suggested_name
            self.spectrum_collected.emit(spectrum, name or suggested_name)
        else:
            self.spectrum_collected.emit(spectrum, suggested_name)

    def _on_collect_cancelled(self, _mode: str) -> None:
        if not self._active:
            return
        self._set_busy(False)

    def _on_collect_failed(self, _mode: str, _message: str) -> None:
        if not self._active:
            return
        self._set_busy(False)

    def _on_preview_ready(self, spectrum, label: str) -> None:
        if not self._active:
            return
        self.preview_ready.emit(spectrum, label)

    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.setEnabled(busy)
        self.bg_btn.setEnabled(not busy and self.controller.connected)
        self.sample_btn.setEnabled(not busy and self.controller.connected)
        if not busy:
            self.progress_bar.setValue(0)

    def _update_background_age_label(self) -> None:
        if not self._active:
            return
        self._refresh_background_age_label()

    def _refresh_background_age_label(self) -> None:
        minutes = self.controller.background_age_minutes()
        if minutes is None:
            self.bg_age_label.setText("No background collected")
            self.bg_age_label.setStyleSheet("")
            return
        self.bg_age_label.setText(f"Background age: {minutes} min")
        if self.controller.background_is_stale():
            self.bg_age_label.setStyleSheet("color: #d62728; font-weight: bold;")
        else:
            self.bg_age_label.setStyleSheet("")

    # -- background save/load ---------------------------------------------------------

    def _update_background_ui(self) -> None:
        self.save_bg_btn.setEnabled(self.controller.background is not None)
        if self._active:
            self._refresh_background_age_label()

    def _background_write_filters(self, io_mod) -> dict:
        """``io_mod.SUPPORTED_WRITE`` with ``.ofjson`` moved first, since
        that's the lossless native format a background should default to."""
        filters = dict(io_mod.SUPPORTED_WRITE)
        ofjson_desc = filters.pop(".ofjson", "OpenFTIR native spectrum (JSON)")
        return {".ofjson": ofjson_desc, **filters}

    def _on_save_background(self) -> None:
        if self.controller.background is None:
            return
        try:
            import openftir.io as io_mod
        except ImportError as exc:
            self._report_error("Save Background", f"File I/O is not available yet:\n{exc}")
            return

        timestamp = self.controller.background_time or datetime.now()
        default_name = f"background_{timestamp:%Y%m%d_%H%M}.ofjson"
        filter_str = file_dialogs.build_filter_string(
            self._background_write_filters(io_mod), include_all_supported=False
        )
        path = file_dialogs.get_save_path(self, "Save Background", default_name, filter_str)
        if not path:
            return
        try:
            self.controller.save_background(path)
        except Exception as exc:  # noqa: BLE001
            self._report_error("Save Background", str(exc))

    def _on_load_background(self) -> None:
        try:
            import openftir.io as io_mod
        except ImportError as exc:
            self._report_error("Load Background", f"File I/O is not available yet:\n{exc}")
            return

        filter_str = file_dialogs.build_filter_string(io_mod.SUPPORTED_READ)
        paths = file_dialogs.get_open_paths(self, "Load Background", filter_str, multiple=False)
        if not paths:
            return
        try:
            self.controller.load_background(paths[0])
        except Exception as exc:  # noqa: BLE001
            self._report_error("Load Background", str(exc))

    # -- helpers ------------------------------------------------------------------------

    def _on_status_message(self, message: str) -> None:
        if not self._active:
            return
        self.status_message.emit(message)

    def _on_error(self, title: str, message: str) -> None:
        if not self._active:
            return
        self._report_error(title, message)

    def _report_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
