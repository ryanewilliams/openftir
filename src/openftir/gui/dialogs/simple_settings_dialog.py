"""Simple mode's "More settings..." dialog: whatever doesn't fit in the top
bar's instrument row or the settings strip (Scans/Resolution/Range/Format --
see ``openftir.gui.simple_panels.SimpleSettingsStrip``). That leaves
apodization, diagnostics, saving/loading a background, and the auto
peak-labelling thresholds.

Everything applies immediately (there is no Ok/Cancel): every settings field
writes straight through to the shared
:class:`~openftir.gui.collect_controller.CollectController` (and, for
auto-labelling, to the callables the dialog is constructed with) via
``update_params``/the given setters, so the top bar's own widgets and the
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
    QVBoxLayout,
)

from openftir.gui import file_dialogs
from openftir.gui.collect_controller import CollectController


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
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)

        # -- acquisition (just what's not already in the settings strip) ---------
        settings_box = QGroupBox("Acquisition")
        settings_form = QFormLayout(settings_box)

        self.apodization_combo = QComboBox()
        self.apodization_combo.addItems(["happ-genzel", "triangular", "boxcar", "blackman-harris"])
        self.apodization_combo.currentTextChanged.connect(lambda t: controller.update_params(apodization=t))
        settings_form.addRow("Apodization:", self.apodization_combo)
        layout.addWidget(settings_box)

        diag_row = QHBoxLayout()
        self.diagnostics_btn = QPushButton("Diagnostics...")
        self.diagnostics_btn.clicked.connect(controller.request_diagnostics)
        diag_row.addWidget(self.diagnostics_btn)
        diag_row.addStretch(1)
        layout.addLayout(diag_row)

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

    # -- connection -----------------------------------------------------------------

    def _refresh_connection_ui(self, connected: bool, _info) -> None:
        self.diagnostics_btn.setEnabled(connected)

    # -- acquisition settings --------------------------------------------------------------

    def _refresh_settings_ui(self) -> None:
        c = self.controller
        self.apodization_combo.blockSignals(True)
        try:
            idx = self.apodization_combo.findText(c.apodization)
            if idx >= 0:
                self.apodization_combo.setCurrentIndex(idx)
        finally:
            self.apodization_combo.blockSignals(False)

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
