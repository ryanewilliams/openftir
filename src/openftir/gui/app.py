"""GUI entry point. Declared as the ``openftir-gui`` script in pyproject.toml
and used by the PyInstaller spec as ``from openftir.gui.app import main``.

Must work with no instrument attached: the main window starts empty with
every instrument-dependent control disabled until the user connects one.
"""

from __future__ import annotations

import os
import sys
import time
from importlib.resources import files

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

# The main window, plotting and processing stack are imported inside main(),
# after the splash screen is up, so the splash appears immediately.


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


def app_icon() -> QIcon:
    """The OpenFTIR logo (packaging/icons/openftir.svg rendered to PNG)."""
    return QIcon(str(files("openftir.gui") / "resources" / "openftir.png"))


def main() -> int:
    QCoreApplication.setOrganizationName("OpenFTIR")
    QCoreApplication.setApplicationName("OpenFTIR")

    app = QApplication.instance() or QApplication(sys.argv)
    apply_light_theme(app)
    app.setWindowIcon(app_icon())
    app.setDesktopFileName("openftir")

    splash = None
    if not os.environ.get("OPENFTIR_NO_SPLASH"):
        from openftir.gui.splash import SplashScreen

        splash = SplashScreen()
        splash.show()
        app.processEvents()
    started = time.monotonic()

    def stage(message: str, progress: float) -> None:
        if splash is not None:
            splash.advance(message, progress)

    stage("Loading spectral processing…", 0.25)
    import openftir.processing  # noqa: F401  (numpy/scipy)

    stage("Loading instrument drivers…", 0.55)
    from openftir.instruments import registry

    registry.available_drivers()

    stage("Preparing workspace…", 0.8)
    from openftir.gui.main_window import MainWindow

    window = MainWindow()

    stage("Ready", 1.0)
    if splash is not None:
        # Keep it up briefly on fast machines so it doesn't just flicker.
        while time.monotonic() - started < 1.0:
            app.processEvents()
            time.sleep(0.02)
    window.show()
    if splash is not None:
        splash.finish(window)

    from openftir.gui.workers import wait_for_workers

    code = app.exec()
    # Let in-flight instrument calls finish instead of aborting mid-thread.
    wait_for_workers()
    return code


if __name__ == "__main__":
    sys.exit(main())
