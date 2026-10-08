from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QVBoxLayout


class DerivativeDialog(QDialog):
    """Parameters for the Savitzky-Golay derivative."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Derivative")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 4)
        self.order_spin.setValue(1)
        form.addRow("Derivative order:", self.order_spin)

        self.window_spin = QSpinBox()
        self.window_spin.setRange(3, 999)
        self.window_spin.setSingleStep(2)
        self.window_spin.setValue(11)
        form.addRow("Window (points):", self.window_spin)

        self.polyorder_spin = QSpinBox()
        self.polyorder_spin.setRange(1, 9)
        self.polyorder_spin.setValue(2)
        form.addRow("Polynomial order:", self.polyorder_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        return {
            "order": self.order_spin.value(),
            "window": self.window_spin.value(),
            "polyorder": self.polyorder_spin.value(),
        }
