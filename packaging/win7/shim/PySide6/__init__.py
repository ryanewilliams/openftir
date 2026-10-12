"""PySide6 stand-in for the Windows 7 build.

Qt 6 does not run on Windows 7, so the Windows 7 build ships Qt 5 (PySide2)
and this package in place of PySide6. It re-exports PySide2 under PySide6's
module names and fills in the few Qt 6 spellings OpenFTIR uses, so the app's
source is the same for every build.
"""

import os

# pyqtgraph picks its Qt binding from what is already imported; point it at
# the real binding rather than this stand-in.
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide2")

import PySide2  # noqa: E402
from PySide2.QtCore import QCoreApplication, Qt  # noqa: E402

__version__ = PySide2.__version__
__version_info__ = PySide2.__version_info__

# Qt 6 scales the UI for high-DPI screens by default; Qt 5 has to be asked
# before the QApplication is created.
QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
