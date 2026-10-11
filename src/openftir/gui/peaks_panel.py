"""Peaks table: shown in Advanced mode's "Peaks" tab and (a second,
narrower instance of the same widget) in Simple mode's side panel -- see
``openftir.gui.main_window.MainWindow._refresh_peaks_panel``.

Two different things populate this table:

- the legacy Process > Peak Picking... dialog's live preview, via
  :meth:`set_peaks` (a plain list of :class:`~openftir.processing.Peak`,
  no auto/manual distinction);
- the normal, always-on display: the selected spectrum's auto-labelled
  peaks merged with its manually picked ones, via :meth:`set_rows`, each
  row tagged "auto" or "manual". Manual rows get a small remove (x)
  button; auto rows don't.
"""

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
from PySide6.QtCore import Signal

from openftir.processing import Peak


class PeaksPanel(QWidget):
    #: emitted with a manual peak's position (cm-1) when its row's remove
    #: (x) button is clicked.
    remove_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self._value_label = "%T / A"
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["cm⁻¹", self._value_label, "Type", ""])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 30)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._table)

        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy the peaks table as tab-separated text")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(copy_btn)

        self._rows: list[tuple[Peak, str]] = []

    # -- population -----------------------------------------------------------------

    def set_rows(self, rows: list[tuple[Peak, str]]) -> None:
        """``rows`` is a list of ``(peak, kind)`` where ``kind`` is
        ``"auto"``, ``"manual"`` or ``""`` (legacy/untagged)."""
        self._rows = list(rows)
        # Drop to 0 rows first rather than just changing the row count:
        # QTableWidget cleans up every existing cell widget (the manual
        # rows' remove buttons) on a row removal, but when a later call
        # reuses the same row index for a *different* row while the table
        # has never been shown (e.g. it's sitting in a background tab --
        # see Advanced mode's Peaks tab), a replaced cell widget can be
        # left behind with stale geometry instead of being torn down. A
        # full reset avoids that regardless of visibility.
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._rows))
        for row, (peak, kind) in enumerate(self._rows):
            self._table.setItem(row, 0, QTableWidgetItem(f"{peak.position:.0f}"))
            self._table.setItem(row, 1, QTableWidgetItem(self._format_value(peak.height)))
            self._table.setItem(row, 2, QTableWidgetItem(kind))
            if kind == "manual":
                remove_btn = QPushButton("×")
                remove_btn.setFixedWidth(22)
                remove_btn.setToolTip("Remove this manual peak")
                remove_btn.clicked.connect(lambda _checked=False, p=peak.position: self.remove_requested.emit(p))
                self._table.setCellWidget(row, 3, remove_btn)
            else:
                self._table.setCellWidget(row, 3, None)

    def _format_value(self, value: float) -> str:
        """%T/%R to one decimal (tiny negative noise shown as 0.0, as in the
        PDF report); absorbance to three decimals."""
        if self._value_label in ("%T", "%R", "%T / A"):
            return "0.0" if value < 0.05 else f"{value:.1f}"
        return f"{value:.3f}"

    def set_value_label(self, label: str) -> None:
        """Column header for the peak value, e.g. "%T" or "Absorbance"."""
        self._value_label = label
        self._table.setHorizontalHeaderItem(1, QTableWidgetItem(label))

    def set_peaks(self, peaks: list[Peak]) -> None:
        """Legacy API for the Process > Peak Picking... dialog's preview:
        plain peaks, no auto/manual tag or remove button."""
        self.set_rows([(p, "") for p in peaks])

    def clear(self) -> None:
        self.set_rows([])

    # -- clipboard --------------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        lines = ["Position\tValue\tType"]
        for peak, kind in self._rows:
            lines.append(f"{peak.position:.2f}\t{peak.height:.4f}\t{kind}")
        QApplication.clipboard().setText("\n".join(lines))
