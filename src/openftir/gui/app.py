"""GUI entry point. Declared as the ``openftir-gui`` script in pyproject.toml
and used by the PyInstaller spec as ``from openftir.gui.app import main``.

Must work with no instrument attached: the main window starts empty with
every instrument-dependent control disabled until the user connects one.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from openftir.gui.main_window import MainWindow
from openftir.gui.workers import wait_for_workers


def _light_palette() -> QPalette:
    """A fixed light ``QPalette``, independent of the OS appearance.

    The plot (white background, dark axes -- see ``openftir.gui.plot``) and
    spectrum trace colours are tuned to read well on a light background;
    following the system dark mode would make them hard to read, so the
    app opts out of dark mode entirely rather than theme every widget.
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return palette


def apply_light_theme(app: QApplication) -> None:
    """Force a light Fusion theme app-wide, regardless of the OS dark-mode
    setting. Call this once, right after creating the ``QApplication``."""
    app.setStyle("Fusion")
    app.setPalette(_light_palette())


def main() -> int:
    QCoreApplication.setOrganizationName("OpenFTIR")
    QCoreApplication.setApplicationName("OpenFTIR")

    app = QApplication.instance() or QApplication(sys.argv)
    apply_light_theme(app)
    window = MainWindow()
    window.show()
    code = app.exec()
    # Let in-flight instrument calls finish instead of aborting mid-thread.
    wait_for_workers()
    return code


if __name__ == "__main__":
    sys.exit(main())
