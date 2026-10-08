"""GUI entry point. Declared as the ``openftir-gui`` script in pyproject.toml
and used by the PyInstaller spec as ``from openftir.gui.app import main``.

Must work with no instrument attached: the main window starts empty with
every instrument-dependent control disabled until the user connects one.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from openftir.gui.main_window import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("OpenFTIR")
    QCoreApplication.setApplicationName("OpenFTIR")

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.resize(1280, 800)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
