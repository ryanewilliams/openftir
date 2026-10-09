"""Settings for the automatic peak-labelling feature (View menu)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
)


class PeakLabelSettingsDialog(QDialog):
    """Threshold/cutoff/prominence controls for ``processing.auto_label_peaks``."""

    def __init__(
        self,
        max_transmittance: float = 85.0,
        min_wavenumber: float = 1500.0,
        min_prominence_t: float = 2.0,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Peak Labelling Settings")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 100.0)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setSuffix(" %T")
        self.threshold_spin.setValue(max_transmittance)
        form.addRow("Label if minimum dips below:", self.threshold_spin)

        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(0.0, 20000.0)
        self.cutoff_spin.setDecimals(0)
        self.cutoff_spin.setSuffix(" cm⁻¹")
        self.cutoff_spin.setValue(min_wavenumber)
        form.addRow("Only label above:", self.cutoff_spin)

        self.prominence_spin = QDoubleSpinBox()
        self.prominence_spin.setRange(0.0, 100.0)
        self.prominence_spin.setDecimals(1)
        self.prominence_spin.setSuffix(" %T")
        self.prominence_spin.setValue(min_prominence_t)
        form.addRow("Minimum prominence:", self.prominence_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        return {
            "max_transmittance": self.threshold_spin.value(),
            "min_wavenumber": self.cutoff_spin.value(),
            "min_prominence_t": self.prominence_spin.value(),
        }
