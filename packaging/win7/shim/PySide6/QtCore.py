from PySide2.QtCore import *  # noqa: F401,F403
from PySide2.QtCore import QCoreApplication, QEventLoop, QThread

from PySide6._compat import add_exec_alias

add_exec_alias(QCoreApplication, QEventLoop, QThread)
