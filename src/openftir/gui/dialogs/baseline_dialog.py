from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class BaselineDialog(QDialog):
    """Method + parameter picker for ALS / polynomial / rubberband baselines.

    Calls ``on_change(method, params)`` whenever a value changes, so the
    caller can draw a live baseline preview before the user accepts.
    """

    def __init__(self, on_change: Callable[[str, dict], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Baseline Correction")
        self._on_change = on_change
        layout = QVBoxLayout(self)

        self.method_combo = QComboBox()
        self.method_combo.addItems(["als", "polynomial", "rubberband"])
        layout.addWidget(self.method_combo)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # -- ALS page --------------------------------------------------------
        als_page = QWidget()
        als_form = QFormLayout(als_page)
        self.lam_spin = QDoubleSpinBox()
        self.lam_spin.setRange(1.0, 1e9)
        self.lam_spin.setDecimals(0)
        self.lam_spin.setValue(1e5)
        als_form.addRow("Smoothness (lambda):", self.lam_spin)
        self.p_spin = QDoubleSpinBox()
        self.p_spin.setRange(0.001, 0.5)
        self.p_spin.setDecimals(3)
        self.p_spin.setSingleStep(0.005)
        self.p_spin.setValue(0.01)
        als_form.addRow("Asymmetry (p):", self.p_spin)
        self._stack.addWidget(als_page)

        # -- Polynomial page --------------------------------------------------
        poly_page = QWidget()
        poly_form = QFormLayout(poly_page)
        self.degree_spin = QSpinBox()
        self.degree_spin.setRange(0, 10)
        self.degree_spin.setValue(2)
        poly_form.addRow("Degree:", self.degree_spin)
        self._stack.addWidget(poly_page)

        # -- Rubberband page (no parameters) -----------------------------------
        rb_page = QWidget()
        QFormLayout(rb_page)
        self._stack.addWidget(rb_page)

        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        self.lam_spin.valueChanged.connect(self._emit_change)
        self.p_spin.valueChanged.connect(self._emit_change)
        self.degree_spin.valueChanged.connect(self._emit_change)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_method_changed(0)

    def _on_method_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._emit_change()

    def _emit_change(self, *_args) -> None:
        method, params = self.method_params()
        self._on_change(method, params)

    def method_params(self) -> tuple[str, dict]:
        method = self.method_combo.currentText()
        if method == "als":
            return method, {"lam": self.lam_spin.value(), "p": self.p_spin.value()}
        if method == "polynomial":
            return method, {"degree": self.degree_spin.value()}
        return method, {}
