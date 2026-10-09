"""Window opens and the basic plot/list/menu structure is sane."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QColor, QPalette, QScreen
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QToolBar

from openftir.gui.app import apply_light_theme
from openftir.gui.main_window import MainWindow

_SETTINGS_ARGS = ("OpenFTIR", "OpenFTIR")


def _use_large_screen(monkeypatch) -> QRect:
    """The offscreen platform's default screen is only 800x800, smaller
    than this app's own minimum layout size -- too small to tell "fit to
    90% of available geometry" apart from "clamped to the app's minimum
    size". Stand in a plausible desktop-sized screen instead."""
    available = QRect(0, 0, 2000, 1400)
    monkeypatch.setattr(QScreen, "availableGeometry", lambda self: available)
    return available


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


def test_full_scale_toolbar_button_removed(qtbot):
    """Panning/zoom is disabled on the plot (always auto-ranges), so a
    'Full Scale' button has nothing left to do."""
    window = MainWindow()
    qtbot.addWidget(window)
    toolbar = window.findChild(QToolBar, "mainToolbar")
    titles = {a.text() for a in toolbar.actions()}
    assert "Full Scale" not in titles
    assert "Stack/Offset" in titles


def test_plot_view_box_has_no_pan_zoom_or_menu(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    vb = window.plot.plot_item.getViewBox()
    assert vb.state["mouseEnabled"] == [False, False]
    assert vb.menuEnabled() in (False, [False, False])
    assert window.plot.plot_item.buttonsHidden is True


def test_plot_aspect_stays_16_9_after_resize(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.resize(1600, 1200)
    qtbot.wait(50)

    w, h = window.plot.width(), window.plot.height()
    assert h > 0
    assert abs((w / h) - (16 / 9)) < 0.05


def test_plot_aspect_stays_16_9_at_small_window_size(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    # Smaller than the other resize test, but not so small that the docks'
    # own minimum sizes dominate and swamp the plot down to a handful of
    # pixels (at which point integer rounding -- not the aspect logic --
    # is what you'd be measuring).
    window.resize(1000, 800)
    qtbot.wait(50)

    w, h = window.plot.width(), window.plot.height()
    assert w > 0 and h > 0
    assert abs((w / h) - (16 / 9)) < 0.05


def test_window_fits_screen_available_geometry_by_default(qtbot, monkeypatch):
    available = _use_large_screen(monkeypatch)
    settings = QSettings(*_SETTINGS_ARGS)
    settings.remove("windowGeometry")

    window = MainWindow()
    qtbot.addWidget(window)

    expected_w = max(1, int(available.width() * 0.9))
    expected_h = max(1, int(available.height() * 0.9))
    assert window.width() == expected_w
    assert window.height() == expected_h


def test_window_restores_saved_geometry_when_it_fits(qtbot, monkeypatch):
    """``restoreGeometry`` itself does its own screen-fit adjustment in C++
    (bypassing a Python-level ``QScreen.availableGeometry`` patch, and
    beholden to the offscreen platform's real, fixed 800x800 screen) so to
    test *our* "does the saved geometry still fit" decision in isolation,
    fake out what restoring applies rather than depending on a real
    round trip through Qt's own screen-aware clamping."""
    available = _use_large_screen(monkeypatch)
    settings = QSettings(*_SETTINGS_ARGS)
    settings.setValue("windowGeometry", b"saved-geometry")

    restored = QRect(available.x() + 5, available.y() + 5, 900, 1200)

    def fake_restore_geometry(self, _data):
        self.setGeometry(restored)
        return True

    monkeypatch.setattr(QMainWindow, "restoreGeometry", fake_restore_geometry)
    try:
        window = MainWindow()
        qtbot.addWidget(window)
        assert window.width() == restored.width()
        assert window.height() == restored.height()
    finally:
        settings.remove("windowGeometry")


def test_window_ignores_saved_geometry_that_no_longer_fits(qtbot):
    settings = QSettings(*_SETTINGS_ARGS)
    try:
        probe = MainWindow()
        qtbot.addWidget(probe)
        probe.setGeometry(200_000, 200_000, 400, 300)  # nowhere near any real screen
        settings.setValue("windowGeometry", probe.saveGeometry())

        window = MainWindow()
        qtbot.addWidget(window)
        available = window.screen().availableGeometry()
        expected_w = max(1, int(available.width() * 0.9))
        assert window.width() == expected_w
    finally:
        settings.remove("windowGeometry")


def test_apply_light_theme_forces_light_palette_even_over_a_dark_one(qtbot):
    app = QApplication.instance()
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.ColorRole.Window, QColor(20, 20, 20))
    app.setPalette(dark_palette)

    apply_light_theme(app)

    window_color = app.palette().color(QPalette.ColorRole.Window)
    assert window_color.lightness() > 200
