from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QVBoxLayout


class SmoothDialog(QDialog):
    """Parameters for Savitzky-Golay smoothing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Savitzky-Golay Smoothing")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.window_spin = QSpinBox()
        self.window_spin.setRange(3, 999)
        self.window_spin.setSingleStep(2)
        self.window_spin.setValue(11)
        form.addRow("Window (points):", self.window_spin)

        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 9)
        self.order_spin.setValue(2)
        form.addRow("Polynomial order:", self.order_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        return {"window": self.window_spin.value(), "order": self.order_spin.value()}
