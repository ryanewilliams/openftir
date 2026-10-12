from PySide2.QtWidgets import *  # noqa: F401,F403
from PySide2.QtWidgets import QApplication, QDialog, QFormLayout, QMenu

from PySide6._compat import add_exec_alias

add_exec_alias(QApplication, QDialog, QMenu)


def _set_row_visible(self, row, on):
    """QFormLayout.setRowVisible (Qt 6.4+): show or hide a whole form row."""
    if not isinstance(row, int):
        row = self.getWidgetPosition(row)[0] if isinstance(row, QWidget) else self.getLayoutPosition(row)[0]
    if row < 0:
        return
    for role in (QFormLayout.LabelRole, QFormLayout.FieldRole, QFormLayout.SpanningRole):
        item = self.itemAt(row, role)
        if item is not None and item.widget() is not None:
            item.widget().setVisible(on)


if not hasattr(QFormLayout, "setRowVisible"):
    QFormLayout.setRowVisible = _set_row_visible


def _is_row_visible(self, row):
    """QFormLayout.isRowVisible (Qt 6.4+)."""
    if not isinstance(row, int):
        row = self.getWidgetPosition(row)[0] if isinstance(row, QWidget) else self.getLayoutPosition(row)[0]
    for role in (QFormLayout.LabelRole, QFormLayout.FieldRole, QFormLayout.SpanningRole):
        item = self.itemAt(row, role)
        if item is not None and item.widget() is not None:
            return not item.widget().isHidden()
    return False


if not hasattr(QFormLayout, "isRowVisible"):
    QFormLayout.isRowVisible = _is_row_visible
