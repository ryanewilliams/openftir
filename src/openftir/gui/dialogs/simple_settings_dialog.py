"""Simple mode's gear-icon settings dialog: a compact superset of Advanced
mode's Collect dock (driver + acquisition settings) plus the auto peak
labelling controls that live in Advanced mode's View menu. No processing
tools here -- those stay in Advanced mode / the Process menu.

Everything applies immediately (there is no Ok/Cancel): connect/disconnect
happen right away, and every settings field writes straight through to the
shared :class:`~openftir.gui.collect_controller.CollectController` (and, for
auto-labelling, to the callables the dialog is constructed with) via
``update_params``/the given setters, so Advanced mode's own widgets and the
plot stay in sync the moment something changes here.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from openftir.gui import file_dialogs
from openftir.gui.collect_controller import CollectController, FORMATS, SIMULATED_SAMPLES


class SimpleSettingsDialog(QDialog):
    def __init__(
        self,
        controller: CollectController,
        get_auto_label: Callable[[], bool],
        set_auto_label: Callable[[bool], None],
        get_peak_label_params: Callable[[], dict],
        set_peak_label_params: Callable[[dict], None],
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self._get_auto_label = get_auto_label
        self._set_auto_label = set_auto_label
        self._get_peak_label_params = get_peak_label_params
        self._set_peak_label_params = set_peak_label_params

        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        # -- instrument ---------------------------------------------------------
        instrument_box = QGroupBox("Instrument")
        form = QFormLayout(instrument_box)

        self.driver_combo = QComboBox()
        self.driver_combo.addItems(controller.available_drivers())
        idx = self.driver_combo.findText(controller.driver_id)
        if idx >= 0:
            self.driver_combo.setCurrentIndex(idx)
        self.driver_combo.currentTextChanged.connect(self._on_driver_changed)
        form.addRow("Driver:", self.driver_combo)

        self.sim_sample_combo = QComboBox()
        self.sim_sample_combo.addItems(SIMULATED_SAMPLES)
        idx = self.sim_sample_combo.findText(controller.sim_sample)
        if idx >= 0:
            self.sim_sample_combo.setCurrentIndex(idx)
        self.sim_sample_combo.currentTextChanged.connect(controller.set_sim_sample)
        form.addRow("Simulated sample:", self.sim_sample_combo)

        conn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(controller.connect_instrument)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(controller.disconnect_instrument)
        self.diagnostics_btn = QPushButton("Diagnostics...")
        self.diagnostics_btn.clicked.connect(controller.request_diagnostics)
        conn_row.addWidget(self.connect_btn)
        conn_row.addWidget(self.disconnect_btn)
        conn_row.addWidget(self.diagnostics_btn)
        form.addRow(conn_row)

        self.status_label = QLabel()
        form.addRow(self.status_label)
        layout.addWidget(instrument_box)

        # -- acquisition settings ------------------------------------------------
        settings_box = QGroupBox("Acquisition")
        settings_form = QFormLayout(settings_box)

        self.scans_spin = QSpinBox()
        self.scans_spin.setRange(1, 10000)
        self.scans_spin.valueChanged.connect(lambda v: controller.update_params(scans=v))
        settings_form.addRow("Scans:", self.scans_spin)

        self.resolution_combo = QComboBox()
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_changed)
        settings_form.addRow("Resolution (cm⁻¹):", self.resolution_combo)

        range_row = QHBoxLayout()
        self.x_min_spin = QDoubleSpinBox()
        self.x_min_spin.setRange(0, 20000)
        self.x_min_spin.valueChanged.connect(lambda v: controller.update_params(x_min=v))
        self.x_max_spin = QDoubleSpinBox()
        self.x_max_spin.setRange(0, 20000)
        self.x_max_spin.valueChanged.connect(lambda v: controller.update_params(x_max=v))
        range_row.addWidget(self.x_min_spin)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.x_max_spin)
        settings_form.addRow("Range (cm⁻¹):", range_row)

        self.apodization_combo = QComboBox()
        self.apodization_combo.addItems(["happ-genzel", "triangular", "boxcar", "blackman-harris"])
        self.apodization_combo.currentTextChanged.connect(lambda t: controller.update_params(apodization=t))
        settings_form.addRow("Apodization:", self.apodization_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(list(FORMATS))
        self.format_combo.currentTextChanged.connect(lambda t: controller.update_params(final_format=t))
        settings_form.addRow("Final format:", self.format_combo)
        layout.addWidget(settings_box)

        # -- background -----------------------------------------------------------
        bg_box = QGroupBox("Background")
        bg_row = QHBoxLayout(bg_box)
        self.save_bg_btn = QPushButton("Save...")
        self.save_bg_btn.clicked.connect(self._on_save_background)
        self.load_bg_btn = QPushButton("Load...")
        self.load_bg_btn.clicked.connect(self._on_load_background)
        bg_row.addWidget(self.save_bg_btn)
        bg_row.addWidget(self.load_bg_btn)
        layout.addWidget(bg_box)

        # -- auto peak labelling ----------------------------------------------------
        label_box = QGroupBox("Peak labels")
        label_form = QFormLayout(label_box)

        self.auto_label_check = QCheckBox("Label peaks automatically")
        self.auto_label_check.setChecked(get_auto_label())
        self.auto_label_check.toggled.connect(set_auto_label)
        label_form.addRow(self.auto_label_check)

        params = get_peak_label_params()
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 100.0)
        self.threshold_spin.setSuffix(" %T")
        self.threshold_spin.setValue(params["max_transmittance"])
        self.threshold_spin.valueChanged.connect(self._on_peak_label_param_changed)
        label_form.addRow("Label if minimum dips below:", self.threshold_spin)

        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(0.0, 20000.0)
        self.cutoff_spin.setDecimals(0)
        self.cutoff_spin.setSuffix(" cm⁻¹")
        self.cutoff_spin.setValue(params["min_wavenumber"])
        self.cutoff_spin.valueChanged.connect(self._on_peak_label_param_changed)
        label_form.addRow("Only label above:", self.cutoff_spin)

        self.prominence_spin = QDoubleSpinBox()
        self.prominence_spin.setRange(0.0, 100.0)
        self.prominence_spin.setSuffix(" %T")
        self.prominence_spin.setValue(params["min_prominence_t"])
        self.prominence_spin.valueChanged.connect(self._on_peak_label_param_changed)
        label_form.addRow("Minimum prominence:", self.prominence_spin)

        layout.addWidget(label_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

        controller.connection_changed.connect(self._refresh_connection_ui)
        controller.settings_changed.connect(self._refresh_settings_ui)
        controller.error.connect(lambda title, msg: QMessageBox.critical(self, title, msg))
        self._refresh_connection_ui(controller.connected, controller.info)
        self._refresh_settings_ui()

    # -- driver / connection --------------------------------------------------------------

    def _on_driver_changed(self, driver_id: str) -> None:
        if driver_id:
            self.controller.set_driver(driver_id)
        is_simulated = driver_id == "simulated"
        self.sim_sample_combo.setVisible(is_simulated)

    def _refresh_connection_ui(self, connected: bool, info) -> None:
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.diagnostics_btn.setEnabled(connected)
        if connected and info is not None:
            self.status_label.setText(f"Connected: {info.vendor} {info.model}")
        else:
            self.status_label.setText("Not connected")

    # -- acquisition settings --------------------------------------------------------------

    def _on_resolution_changed(self, _index: int) -> None:
        value = self.resolution_combo.currentData()
        if value is not None:
            self.controller.update_params(resolution=float(value))

    def _refresh_settings_ui(self) -> None:
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
            self.resolution_combo.clear()
            for r in c.resolutions:
                self.resolution_combo.addItem(f"{r:g}", r)
            idx = self.resolution_combo.findData(c.resolution)
            if idx >= 0:
                self.resolution_combo.setCurrentIndex(idx)
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

    # -- background -----------------------------------------------------------------------

    def _on_save_background(self) -> None:
        if self.controller.background is None:
            QMessageBox.information(self, "Save Background", "No background collected yet.")
            return
        timestamp = self.controller.background_time
        default_name = f"background_{timestamp:%Y%m%d_%H%M}.ofjson" if timestamp else "background.ofjson"
        try:
            import openftir.io as io_mod

            filters = dict(io_mod.SUPPORTED_WRITE)
            ofjson_desc = filters.pop(".ofjson", "OpenFTIR native spectrum (JSON)")
            filters = {".ofjson": ofjson_desc, **filters}
        except ImportError:
            filters = {".ofjson": "OpenFTIR native spectrum (JSON)"}
        filter_str = file_dialogs.build_filter_string(filters, include_all_supported=False)
        path = file_dialogs.get_save_path(self, "Save Background", default_name, filter_str)
        if not path:
            return
        try:
            self.controller.save_background(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save Background", str(exc))

    def _on_load_background(self) -> None:
        try:
            import openftir.io as io_mod

            filters = io_mod.SUPPORTED_READ
        except ImportError:
            filters = {}
        filter_str = file_dialogs.build_filter_string(filters)
        paths = file_dialogs.get_open_paths(self, "Load Background", filter_str, multiple=False)
        if not paths:
            return
        try:
            self.controller.load_background(paths[0])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load Background", str(exc))

    # -- peak labelling ---------------------------------------------------------------------

    def _on_peak_label_param_changed(self, _value: float) -> None:
        self._set_peak_label_params(
            {
                "max_transmittance": self.threshold_spin.value(),
                "min_wavenumber": self.cutoff_spin.value(),
                "min_prominence_t": self.prominence_spin.value(),
            }
        )
