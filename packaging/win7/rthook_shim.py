"""PyInstaller runtime hook: make ``import PySide6`` load shim/ (Qt 5).

The stand-in ships as plain files in ``win7shim/`` rather than being frozen,
so PyInstaller never mistakes it for the real PySide6 and applies PySide6's
packaging rules to it.
"""

import os
import sys

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide2")
sys.path.insert(0, os.path.join(sys._MEIPASS, "win7shim"))
