"""A compact dialog for the PDF report export: sample name + paper size.

The actual save-location prompt is the app's own non-native save dialog
(``openftir.gui.file_dialogs``), shown by the caller after this one is
accepted -- see ``MainWindow._on_export_pdf_report``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

_PAPER_SIZES = ["Letter", "A4"]


class ReportDialog(QDialog):
    """Prompts for the report's Sample name and paper size."""

    def __init__(self, default_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export PDF Report")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(default_name)
        self.name_edit.selectAll()
        form.addRow("Sample name:", self.name_edit)

        self.paper_combo = QComboBox()
        self.paper_combo.addItems(_PAPER_SIZES)
        form.addRow("Paper:", self.paper_combo)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def sample_name(self) -> str:
        return self.name_edit.text().strip()

    def paper(self) -> str:
        """``"letter"`` or ``"a4"`` -- matches ``report.export_report_pdf``'s
        ``paper`` argument."""
        return self.paper_combo.currentText().strip().lower()
