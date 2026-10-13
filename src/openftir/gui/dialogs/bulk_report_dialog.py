"""Export PDF reports for several spectra at once.

Lists every spectrum that can have a report, each with a tick box and an
editable sample name, plus one folder and paper size for the lot. The
caller (``MainWindow._on_export_pdf_reports``) writes the files.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from openftir.gui import file_dialogs
from openftir.spectrum import local_naive

_PAPER_SIZES = ["Letter", "A4"]


@dataclass
class BulkReportItem:
    row: int
    name: str
    acquired: object = None


class BulkReportDialog(QDialog):
    def __init__(self, items: list[BulkReportItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export PDF Reports")
        self.resize(560, 420)
        self._items = items

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tick the samples to export. Click a name to change it."))

        self.table = QTableWidget(len(items), 2, self)
        self.table.setHorizontalHeaderLabels(["Sample name", "Collected"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for i, item in enumerate(items):
            name = QTableWidgetItem(item.name)
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, name)
            when = local_naive(item.acquired)
            collected = QTableWidgetItem(when.strftime("%d %b %H:%M") if when else "—")
            collected.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(i, 1, collected)
        self.table.itemChanged.connect(self._update_ok_button)
        layout.addWidget(self.table, 1)

        ticks = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all(False))
        ticks.addWidget(select_all)
        ticks.addWidget(select_none)
        ticks.addStretch(1)
        ticks.addWidget(QLabel("Paper:"))
        self.paper_combo = QComboBox()
        self.paper_combo.addItems(_PAPER_SIZES)
        ticks.addWidget(self.paper_combo)
        layout.addLayout(ticks)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Save to:"))
        self.folder_edit = QLineEdit(file_dialogs.default_directory())
        self.folder_edit.setReadOnly(True)
        folder_row.addWidget(self.folder_edit, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(choose)
        layout.addLayout(folder_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_ok_button()
        # Start with nothing being edited; Enter exports.
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setDefault(True)
        ok.setFocus()

    def _set_all(self, checked: bool) -> None:
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _choose_folder(self) -> None:
        folder = file_dialogs.get_existing_directory(self, "Save Reports To")
        if folder:
            self.folder_edit.setText(folder)

    def _update_ok_button(self, *_args) -> None:
        count = len(self.selected())
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Export {count} report{'s' if count != 1 else ''}")
        ok.setEnabled(count > 0)

    def selected(self) -> list[BulkReportItem]:
        """The ticked spectra, with their (possibly edited) names."""
        chosen = []
        for i, item in enumerate(self._items):
            cell = self.table.item(i, 0)
            if cell.checkState() == Qt.Checked:
                name = cell.text().strip() or item.name
                chosen.append(BulkReportItem(item.row, name, item.acquired))
        return chosen

    def folder(self) -> str:
        return self.folder_edit.text()

    def paper(self) -> str:
        return self.paper_combo.currentText().strip().lower()
