from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
)

_METHODS = ["max", "minmax", "area", "vector", "peak"]


class NormalizeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Normalize")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.method_combo = QComboBox()
        self.method_combo.addItems(_METHODS)
        form.addRow("Method:", self.method_combo)

        self.at_spin = QDoubleSpinBox()
        self.at_spin.setRange(-1e6, 1e6)
        self.at_spin.setDecimals(2)
        self.at_spin.setValue(1600.0)
        self.at_spin.setEnabled(False)
        form.addRow("At x =", self.at_spin)

        self.method_combo.currentTextChanged.connect(
            lambda text: self.at_spin.setEnabled(text == "peak")
        )

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        method = self.method_combo.currentText()
        params = {"method": method}
        if method == "peak":
            params["at"] = self.at_spin.value()
        return params
