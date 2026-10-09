"""Spectral library dock: open/create a library folder, add entries, search."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openftir.gui import file_dialogs
from openftir.processing import Match, SpectralLibrary, search
from openftir.spectrum import Spectrum


class LibraryPanel(QWidget):
    """Lets the user manage a :class:`~openftir.processing.SpectralLibrary`."""

    #: emitted when the user clicks a search result to overlay it (spectrum, name)
    overlay_requested = Signal(object, str)

    def __init__(self, get_selected: Callable[[], tuple[str, Spectrum] | None], parent=None):
        super().__init__(parent)
        self._get_selected = get_selected
        self._library: SpectralLibrary | None = None
        self._matches: list[Match] = []

        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        self._path_label = QLabel("No library open")
        self._path_label.setWordWrap(True)
        path_row.addWidget(self._path_label, 1)
        layout.addLayout(path_row)

        open_row = QHBoxLayout()
        open_btn = QPushButton("Open...")
        open_btn.clicked.connect(self._on_open)
        new_btn = QPushButton("New...")
        new_btn.clicked.connect(self._on_create)
        open_row.addWidget(open_btn)
        open_row.addWidget(new_btn)
        layout.addLayout(open_row)

        action_row = QHBoxLayout()
        self._add_btn = QPushButton("Add selected to library")
        self._add_btn.clicked.connect(self._on_add)
        self._search_btn = QPushButton("Search selected")
        self._search_btn.clicked.connect(self._on_search)
        action_row.addWidget(self._add_btn)
        action_row.addWidget(self._search_btn)
        layout.addLayout(action_row)

        self._results_table = QTableWidget(0, 2)
        self._results_table.setHorizontalHeaderLabels(["Name", "Score"])
        self._results_table.horizontalHeader().setStretchLastSection(True)
        self._results_table.cellDoubleClicked.connect(self._on_result_clicked)
        layout.addWidget(self._results_table)

        self._set_library(None)

    # -- library open/create -----------------------------------------------------

    def _set_library(self, library: SpectralLibrary | None) -> None:
        self._library = library
        has_lib = library is not None
        self._path_label.setText(str(library.path) if has_lib else "No library open")
        self._add_btn.setEnabled(has_lib)
        self._search_btn.setEnabled(has_lib)

    def _on_open(self) -> None:
        directory = file_dialogs.get_existing_directory(self, "Open Spectral Library")
        if not directory:
            return
        try:
            self._set_library(SpectralLibrary.open(directory))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open Library", f"Could not open library:\n{exc}")

    def _on_create(self) -> None:
        directory = file_dialogs.get_existing_directory(self, "Create Spectral Library")
        if not directory:
            return
        try:
            self._set_library(SpectralLibrary.create(directory))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Create Library", f"Could not create library:\n{exc}")

    # -- add / search --------------------------------------------------------------

    def _on_add(self) -> None:
        if self._library is None:
            return
        selected = self._get_selected()
        if selected is None:
            QMessageBox.information(self, "Add to Library", "Select a spectrum first.")
            return
        name, spectrum = selected
        try:
            final_name = self._library.add(spectrum, name=name)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Add to Library", f"Could not add spectrum:\n{exc}")
            return
        QMessageBox.information(self, "Add to Library", f"Added as '{final_name}'.")

    def _on_search(self) -> None:
        if self._library is None:
            return
        selected = self._get_selected()
        if selected is None:
            QMessageBox.information(self, "Search Library", "Select a spectrum first.")
            return
        _, spectrum = selected
        try:
            self._matches = search(spectrum, self._library)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Search Library", f"Search failed:\n{exc}")
            return
        self._results_table.setRowCount(len(self._matches))
        for row, match in enumerate(self._matches):
            self._results_table.setItem(row, 0, QTableWidgetItem(match.name))
            self._results_table.setItem(row, 1, QTableWidgetItem(f"{match.score:.4f}"))

    def _on_result_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._matches):
            match = self._matches[row]
            self.overlay_requested.emit(match.spectrum, match.name)
