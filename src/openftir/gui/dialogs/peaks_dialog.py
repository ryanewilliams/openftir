from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSlider,
    QVBoxLayout,
)


class PeaksDialog(QDialog):
    """Prominence/distance/threshold controls with a live preview callback."""

    def __init__(self, y_span: float, on_change: Callable[[dict], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Peak Picking")
        self._on_change = on_change
        self._max_prominence = max(y_span, 1e-6)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.prominence_slider = QSlider(Qt.Horizontal)
        self.prominence_slider.setRange(0, 1000)
        self.prominence_slider.setValue(50)
        self.prominence_slider.valueChanged.connect(self._emit_change)
        form.addRow("Prominence:", self.prominence_slider)

        self.min_distance_spin = QDoubleSpinBox()
        self.min_distance_spin.setRange(0.0, 1e5)
        self.min_distance_spin.setDecimals(1)
        self.min_distance_spin.setValue(0.0)
        self.min_distance_spin.valueChanged.connect(self._emit_change)
        form.addRow("Min distance (cm⁻¹):", self.min_distance_spin)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(-1e6, 1e6)
        self.threshold_spin.setDecimals(4)
        self.threshold_spin.setValue(0.0)
        form.addRow("Min height (0 = none):", self.threshold_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._emit_change()

    def _emit_change(self, *_args) -> None:
        self._on_change(self.params())

    def params(self) -> dict:
        prominence = (self.prominence_slider.value() / 1000.0) * self._max_prominence
        params = {"prominence": prominence}
        if self.min_distance_spin.value() > 0:
            params["min_distance_cm"] = self.min_distance_spin.value()
        if self.threshold_spin.value() != 0:
            params["threshold"] = self.threshold_spin.value()
        return params
