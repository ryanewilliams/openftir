from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
)


class SubtractDialog(QDialog):
    """Pick a spectrum B and a scale factor for ``A - factor * B``."""

    def __init__(self, choices: list[tuple[int, str]], auto_factor: Callable[[int], float], parent=None):
        """``choices``: list of ``(row, display name)`` for every candidate B.
        ``auto_factor(row)`` computes the least-squares factor against A.
        """
        super().__init__(parent)
        self.setWindowTitle("Subtract Spectrum")
        self._auto_factor = auto_factor
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.b_combo = QComboBox()
        for row, name in choices:
            self.b_combo.addItem(name, row)
        form.addRow("Subtract:", self.b_combo)

        factor_row = QHBoxLayout()
        self.factor_spin = QDoubleSpinBox()
        self.factor_spin.setRange(-1e6, 1e6)
        self.factor_spin.setDecimals(4)
        self.factor_spin.setValue(1.0)
        factor_row.addWidget(self.factor_spin)
        auto_btn = QPushButton("Auto factor")
        auto_btn.clicked.connect(self._on_auto_factor)
        factor_row.addWidget(auto_btn)
        form.addRow("Factor:", factor_row)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_auto_factor(self) -> None:
        row = self.b_combo.currentData()
        if row is None:
            return
        try:
            factor = self._auto_factor(row)
        except Exception:
            return
        self.factor_spin.setValue(factor)

    def params(self) -> dict:
        return {"b_row": self.b_combo.currentData(), "factor": self.factor_spin.value()}
