from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QSpinBox, QVBoxLayout


class AtrDialog(QDialog):
    """Parameters for the simplified ATR penetration-depth correction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ATR Correction")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.n_crystal_spin = QDoubleSpinBox()
        self.n_crystal_spin.setRange(1.0, 5.0)
        self.n_crystal_spin.setDecimals(2)
        self.n_crystal_spin.setValue(2.4)
        form.addRow("Crystal index (n1):", self.n_crystal_spin)

        self.n_sample_spin = QDoubleSpinBox()
        self.n_sample_spin.setRange(1.0, 5.0)
        self.n_sample_spin.setDecimals(2)
        self.n_sample_spin.setValue(1.5)
        form.addRow("Sample index (n2):", self.n_sample_spin)

        self.angle_spin = QDoubleSpinBox()
        self.angle_spin.setRange(1.0, 89.0)
        self.angle_spin.setDecimals(1)
        self.angle_spin.setValue(45.0)
        form.addRow("Incidence angle (deg):", self.angle_spin)

        self.reflections_spin = QSpinBox()
        self.reflections_spin.setRange(1, 20)
        self.reflections_spin.setValue(1)
        form.addRow("Reflections:", self.reflections_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        return {
            "n_crystal": self.n_crystal_spin.value(),
            "n_sample": self.n_sample_spin.value(),
            "angle_deg": self.angle_spin.value(),
            "reflections": self.reflections_spin.value(),
        }
