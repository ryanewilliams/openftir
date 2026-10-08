"""Spectrum details dock: units, range, metadata and processing history."""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openftir.spectrum import Spectrum


class DetailsPanel(QWidget):
    """Read-only summary of the currently selected spectrum."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        info_box = QGroupBox("Spectrum")
        form = QFormLayout(info_box)
        self._title = QLabel("–")
        self._units = QLabel("–")
        self._points = QLabel("–")
        self._range = QLabel("–")
        self._acquired = QLabel("–")
        form.addRow("Title:", self._title)
        form.addRow("Units:", self._units)
        form.addRow("Points:", self._points)
        form.addRow("Range:", self._range)
        form.addRow("Acquired:", self._acquired)
        layout.addWidget(info_box)

        meta_box = QGroupBox("Metadata")
        meta_layout = QVBoxLayout(meta_box)
        self._meta_table = QTableWidget(0, 2)
        self._meta_table.setHorizontalHeaderLabels(["Key", "Value"])
        self._meta_table.horizontalHeader().setStretchLastSection(True)
        self._meta_table.verticalHeader().setVisible(False)
        meta_layout.addWidget(self._meta_table)
        layout.addWidget(meta_box)

        history_box = QGroupBox("Processing history")
        history_layout = QVBoxLayout(history_box)
        self._history_list = QListWidget()
        history_layout.addWidget(self._history_list)
        layout.addWidget(history_box)

        self.clear()

    def clear(self) -> None:
        self._title.setText("–")
        self._units.setText("–")
        self._points.setText("–")
        self._range.setText("–")
        self._acquired.setText("–")
        self._meta_table.setRowCount(0)
        self._history_list.clear()

    def show_spectrum(self, name: str, spectrum: Spectrum) -> None:
        self._title.setText(name)
        self._units.setText(f"{spectrum.x_unit.value} / {spectrum.y_unit.value}")
        self._points.setText(str(len(spectrum)))
        if len(spectrum):
            self._range.setText(f"{np.min(spectrum.x):.1f} – {np.max(spectrum.x):.1f} {spectrum.x_unit.value}")
        else:
            self._range.setText("–")
        self._acquired.setText(spectrum.acquired.isoformat(sep=" ") if spectrum.acquired else "unknown")

        self._meta_table.setRowCount(len(spectrum.meta))
        for row, (key, value) in enumerate(sorted(spectrum.meta.items())):
            self._meta_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self._meta_table.setItem(row, 1, QTableWidgetItem(str(value)))

        self._history_list.clear()
        self._history_list.addItems(spectrum.history or ["(no processing applied)"])
