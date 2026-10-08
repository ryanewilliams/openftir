"""Window opens and the basic plot/list/menu structure is sane."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDockWidget

from openftir.gui.main_window import MainWindow


def test_main_window_opens(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.windowTitle() == "OpenFTIR"
    assert window.plot is not None
    assert window.model.rowCount() == 0

    dock_names = {d.objectName() for d in window.findChildren(QDockWidget)}
    assert {"spectraDock", "detailsDock", "peaksDock", "collectDock", "libraryDock"} <= dock_names

    assert window.menuBar().findChild(object, None) is not None  # menu bar populated
    menu_titles = {a.text().replace("&", "") for a in window.menuBar().actions()}
    assert {"File", "Process"} <= menu_titles


def test_main_window_works_with_no_instrument(qtbot):
    """The app must start fine with nothing connected."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.collect_panel.disconnect_btn.isEnabled() is False
    assert window.collect_panel.sample_btn.isEnabled() is False
