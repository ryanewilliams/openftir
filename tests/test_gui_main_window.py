"""Window opens and the basic plot/list/menu structure is sane."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSettings, Qt
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
    assert {"spectraDock", "collectDock", "libraryDock"} <= dock_names
    # Peaks and Details live in tabs under the plot, not in docks.
    tabs = window._central.tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Peaks", "Details"]

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


@pytest.mark.parametrize("size", [(1600, 1000), (1100, 750)])
def test_simple_mode_plot_fills_the_width_beside_the_peaks_panel(qtbot, size):
    # In the app the plot stretches to use the space (exports stay 16:9, see
    # test_plot_image_export_is_16_9_however_the_plot_is_stretched).
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._apply_mode("simple")
    window.resize(*size)
    qtbot.wait(50)
    body = window._simple_central._body
    plot_box = window._central.plot_container.geometry()
    panel = body.panel.geometry()
    assert plot_box.height() == body.height()  # full height
    assert panel.right() >= body.width() - 2  # no empty strip on the right
    assert window.plot.width() == plot_box.width()  # plot fills its box


def test_advanced_mode_plot_spans_the_central_width(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._apply_mode("advanced")
    window.resize(1400, 900)
    qtbot.wait(50)
    assert window.plot.width() == window._central.width()
    assert window._central.tabs.height() >= 150


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
    settings.setValue("windowRect", restored)

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
        settings.remove("windowRect")


def test_window_ignores_saved_geometry_that_no_longer_fits(qtbot):
    settings = QSettings(*_SETTINGS_ARGS)
    try:
        probe = MainWindow()
        qtbot.addWidget(probe)
        probe.setGeometry(200_000, 200_000, 400, 300)  # nowhere near any real screen
        settings.setValue("windowGeometry", probe.saveGeometry())
        settings.setValue("windowRect", probe.frameGeometry())

        window = MainWindow()
        qtbot.addWidget(window)
        available = window.screen().availableGeometry()
        expected_w = max(1, int(available.width() * 0.9))
        assert window.width() == expected_w
    finally:
        settings.remove("windowGeometry")
        settings.remove("windowRect")


def test_apply_light_theme_forces_light_palette_even_over_a_dark_one(qtbot):
    app = QApplication.instance()
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.ColorRole.Window, QColor(20, 20, 20))
    app.setPalette(dark_palette)

    apply_light_theme(app)

    window_color = app.palette().color(QPalette.ColorRole.Window)
    assert window_color.lightness() > 200


def test_plot_sits_at_top_of_central_area(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("advanced")  # this layout (plot + Peaks/Details tabs) is Advanced-mode-only
    window.resize(1400, 900)
    window.show()
    qtbot.wait(50)
    container = window._central.plot_container
    assert container.y() == 0
    assert window.plot.y() == 0
    # The tabs below take the remaining space instead of empty bands.
    assert window._central.tabs.y() >= container.height()


@pytest.mark.parametrize("mode", ["advanced", "simple"])
def test_maximise_then_shrink_keeps_everything_on_screen(qtbot, mode):
    # Regression: the plot's height was pinned with setFixedHeight (which is
    # also a minimum), so after the window had been large it could not shrink
    # back and maximising pushed the Peaks/Details tabs off screen.
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._apply_mode(mode)
    for size in ((2600, 1500), (1100, 700), (2600, 1500), (1000, 650)):
        window.resize(*size)
        qtbot.wait(30)
    assert window.height() <= 650 + 1
    assert window.minimumSizeHint().height() < 650
    central = window._central if mode == "advanced" else window._simple_central
    assert central.geometry().bottom() <= window.height()
    plot_box = window._central.plot_container
    # The plot's box must sit inside its parent, and in Advanced mode the
    # tabs must keep at least their minimum height below it.
    assert plot_box.geometry().bottom() <= plot_box.parent().height()
    if mode == "advanced":
        tabs = window._central.tabs
        assert tabs.height() >= 150
        assert tabs.geometry().bottom() <= window._central.height()


def test_hidden_mode_does_not_inflate_minimum_window_size(qtbot):
    # Regression: the stacked central widget took the larger minimum of both
    # modes, so Advanced mode could not be made narrower than Simple mode's
    # top bar plus Advanced's docks (~1650 px). Compare against what the
    # visible parts need rather than a pixel constant: text widths vary a lot
    # between platforms (Windows CI's offscreen fonts are ~1.9x wider).
    from PySide6.QtWidgets import QDockWidget

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._apply_mode("advanced")
    qtbot.wait(20)

    assert window.centralWidget().minimumSizeHint().width() <= window._central.minimumSizeHint().width() + 4

    docks = [d for d in window.findChildren(QDockWidget) if d.isVisible()]
    left = max((d.minimumSizeHint().width() for d in docks if window.dockWidgetArea(d) == Qt.LeftDockWidgetArea), default=0)
    right = max((d.minimumSizeHint().width() for d in docks if window.dockWidgetArea(d) == Qt.RightDockWidgetArea), default=0)
    needed = left + right + window._central.minimumSizeHint().width()
    assert window.minimumSizeHint().width() <= needed + 60  # separators/margins


def test_log_file_records_openftir_messages(tmp_path):
    import logging

    from openftir.gui.logfile import start_logging

    path = start_logging(tmp_path / "logs" / "openftir.log")
    try:
        logging.getLogger("openftir.instruments.varian.driver").info("aligned the bench")
        for handler in logging.getLogger("openftir").handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "started" in text and "aligned the bench" in text
    finally:
        logger = logging.getLogger("openftir")
        for handler in list(logger.handlers):
            if getattr(handler, "baseFilename", "") == str(path):
                logger.removeHandler(handler)
                handler.close()
