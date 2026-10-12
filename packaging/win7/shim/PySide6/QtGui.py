from PySide2.QtGui import *  # noqa: F401,F403
from PySide2.QtGui import QGuiApplication

# Qt 6 moved these from QtWidgets to QtGui.
from PySide2.QtWidgets import QAction, QActionGroup, QShortcut, QUndoCommand, QUndoStack  # noqa: F401

from PySide6._compat import add_exec_alias

add_exec_alias(QGuiApplication)
