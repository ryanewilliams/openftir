from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


class CropDialog(QDialog):
    """Numeric x-axis crop range, defaulting to the spectrum's own full range.

    Replaces cropping "to the current view": since the main plot no longer
    supports panning/zooming, there is no view range to crop to.
    """

    def __init__(self, x_min: float, x_max: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crop")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        lo, hi = (x_min, x_max) if x_min <= x_max else (x_max, x_min)
        span = max(hi - lo, 1.0)

        self.x_min_spin = QDoubleSpinBox()
        self.x_min_spin.setRange(lo - span, hi + span)
        self.x_min_spin.setDecimals(2)
        self.x_min_spin.setValue(lo)

        self.x_max_spin = QDoubleSpinBox()
        self.x_max_spin.setRange(lo - span, hi + span)
        self.x_max_spin.setDecimals(2)
        self.x_max_spin.setValue(hi)

        range_row = QHBoxLayout()
        range_row.addWidget(self.x_min_spin)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.x_max_spin)
        form.addRow("Range (cm⁻¹):", range_row)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        return {"x_min": self.x_min_spin.value(), "x_max": self.x_max_spin.value()}
