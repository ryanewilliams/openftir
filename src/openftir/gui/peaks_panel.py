"""Dock showing the results of the most recent peak-picking run."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openftir.processing import Peak


class PeaksPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Position", "Height", "Width"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._table)

        copy_btn = QPushButton("Copy to clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_btn)

        self._peaks: list[Peak] = []

    def set_peaks(self, peaks: list[Peak]) -> None:
        self._peaks = list(peaks)
        self._table.setRowCount(len(peaks))
        for row, peak in enumerate(peaks):
            self._table.setItem(row, 0, QTableWidgetItem(f"{peak.position:.2f}"))
            self._table.setItem(row, 1, QTableWidgetItem(f"{peak.height:.4f}"))
            width_text = f"{peak.width:.2f}" if peak.width is not None else ""
            self._table.setItem(row, 2, QTableWidgetItem(width_text))

    def clear(self) -> None:
        self.set_peaks([])

    def _copy_to_clipboard(self) -> None:
        lines = ["Position\tHeight\tWidth"]
        for peak in self._peaks:
            width_text = f"{peak.width:.2f}" if peak.width is not None else ""
            lines.append(f"{peak.position:.2f}\t{peak.height:.4f}\t{width_text}")
        QApplication.clipboard().setText("\n".join(lines))
