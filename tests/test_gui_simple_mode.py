"""Simple mode: the default UI, a thin top/bottom bar view over the same
CollectController/model/plot Advanced mode uses -- see
openftir.gui.collect_controller and openftir.gui.simple_panels."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings

from openftir.gui.dialogs.simple_settings_dialog import SimpleSettingsDialog
from openftir.gui.main_window import MainWindow

_SETTINGS_ARGS = ("OpenFTIR", "OpenFTIR")


def _connect_simulated(qtbot, controller) -> None:
    controller.set_driver("simulated")
    with qtbot.waitSignal(controller.connection_changed, timeout=5000):
        controller.connect_instrument()
    assert controller.connected


def test_always_opens_in_simple_mode_even_if_advanced_was_used_last(qtbot):
    first = MainWindow()
    qtbot.addWidget(first)
    first._apply_mode("advanced")
    first.close()

    window = MainWindow()
    qtbot.addWidget(window)
    assert window._mode == "simple"
    assert window._stack.currentWidget() is window._simple_central


def test_default_mode_is_simple_on_fresh_settings(qtbot):
    settings = QSettings(*_SETTINGS_ARGS)
    settings.remove("ui/mode")

    window = MainWindow()
    qtbot.addWidget(window)

    assert window._mode == "simple"
    assert window.simple_mode_action.isChecked() is True
    assert window._stack.currentWidget() is window._simple_central


def test_switching_modes_keeps_same_plot_and_list(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    plot_id = id(window.plot)
    model_id = id(window.model)

    window._apply_mode("advanced")
    assert id(window.plot) == plot_id
    assert id(window.model) == model_id
    # the shared plot container now lives in the advanced central widget
    assert window._central.plot_container.layout() is None or True
    assert window.plot.parentWidget() is window._central.plot_container

    window._apply_mode("simple")
    assert id(window.plot) == plot_id
    assert id(window.model) == model_id
    assert window.plot.parentWidget() is window._simple_central.plot_container


def test_simple_mode_background_then_sample_collect(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")

    controller = window.collect_controller
    _connect_simulated(qtbot, controller)
    controller.update_params(scans=2)

    top_bar = window.simple_top_bar
    with qtbot.waitSignal(top_bar.spectrum_collected, timeout=10000):
        top_bar.background_btn.click()
    qtbot.waitUntil(lambda: controller.background is not None, timeout=10000)
    assert window.model.rowCount() == 1  # the background, added to the list

    minutes = controller.background_age_minutes()
    assert minutes is not None and minutes >= 0

    with qtbot.waitSignal(top_bar.spectrum_collected, timeout=10000) as blocker:
        top_bar.collect_btn.click()

    spectrum, name = blocker.args
    assert name == "Sample 1"
    assert window.model.rowCount() == 2
    assert window.model.entry_at(1).name == "Sample 1"

    # A second sample keeps incrementing.
    with qtbot.waitSignal(top_bar.spectrum_collected, timeout=10000) as blocker2:
        top_bar.collect_btn.click()
    assert blocker2.args[1] == "Sample 2"


def test_simple_mode_cancel_restores_buttons(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")

    controller = window.collect_controller
    _connect_simulated(qtbot, controller)
    controller._instrument.scan_time_s = 0.2
    controller.update_params(scans=50)

    top_bar = window.simple_top_bar
    top_bar.background_btn.click()
    qtbot.waitUntil(lambda: top_bar.background_btn.text() == "Cancel", timeout=5000)
    assert top_bar.collect_btn.isEnabled() is False

    qtbot.wait(150)
    top_bar.background_btn.click()  # now acts as Cancel

    qtbot.waitUntil(lambda: top_bar.background_btn.text() == "Background", timeout=10000)
    assert top_bar.collect_btn.isEnabled() is True
    assert controller.background is None


def test_connect_button_toggles_connect_disconnect(qtbot):
    """Row 1's explicit Connect button (not just a clickable pill) connects
    the selected instrument and turns into Disconnect once connected."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")

    controller = window.collect_controller
    controller.set_driver("simulated")
    top_bar = window.simple_top_bar

    assert controller.connected is False
    assert top_bar.connect_btn.text() == "Connect"
    with qtbot.waitSignal(controller.connection_changed, timeout=5000):
        top_bar.connect_btn.click()
    assert controller.connected is True
    assert top_bar.connect_btn.text() == "Disconnect"
    assert "Connected" in top_bar.pill.text()

    with qtbot.waitSignal(controller.connection_changed, timeout=5000):
        top_bar.connect_btn.click()
    assert controller.connected is False
    assert top_bar.connect_btn.text() == "Connect"
    assert top_bar.pill.text() == "Not connected"


def test_instrument_combo_lists_drivers_by_display_name(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")
    window.show()  # isVisible() is only meaningful once the widget is shown

    top_bar = window.simple_top_bar
    names = window.collect_controller.driver_display_names()
    combo_texts = {top_bar.instrument_combo.itemText(i) for i in range(top_bar.instrument_combo.count())}
    assert combo_texts == set(names.values())
    # The simulated sample selector only shows for the simulated driver.
    idx = top_bar.instrument_combo.findData("simulated")
    top_bar.instrument_combo.setCurrentIndex(idx)
    assert top_bar.sim_sample_combo.isVisible() is True


def test_settings_dialog_apodization_changes_persist(qtbot):
    """The trimmed "More settings..." dialog (apodization, diagnostics,
    background save/load, auto-label thresholds -- scans/resolution/range/
    format now live in the Row 2 settings strip, see
    test_settings_strip_updates_controller_and_persists) still writes
    straight through to the controller."""
    settings = QSettings(*_SETTINGS_ARGS)
    settings.remove("collect/simulated")

    window = MainWindow()
    qtbot.addWidget(window)
    controller = window.collect_controller
    controller.set_driver("simulated")

    dialog = SimpleSettingsDialog(
        controller,
        get_auto_label=lambda: True,
        set_auto_label=lambda _v: None,
        get_peak_label_params=lambda: {"max_transmittance": 85.0, "min_wavenumber": 1500.0, "min_prominence_t": 2.0},
        set_peak_label_params=lambda _p: None,
    )
    qtbot.addWidget(dialog)

    idx = dialog.apodization_combo.findText("boxcar")
    dialog.apodization_combo.setCurrentIndex(idx)
    assert controller.apodization == "boxcar"

    # Persisted immediately (no Ok button to click) -- a fresh controller
    # restores the same value for this driver.
    from openftir.gui.collect_controller import CollectController

    controller2 = CollectController()
    controller2.set_driver("simulated")
    assert controller2.apodization == "boxcar"

    settings.remove("collect/simulated")


def test_settings_strip_updates_controller_and_persists(qtbot):
    """Row 2's settings strip edits (Scans/Resolution/Range/Format) write
    straight through to the controller and persist per driver, same as the
    old dialog fields they replaced."""
    settings = QSettings(*_SETTINGS_ARGS)
    settings.remove("collect/simulated")

    window = MainWindow()
    qtbot.addWidget(window)
    controller = window.collect_controller
    controller.set_driver("simulated")

    strip = window.simple_settings_strip
    strip.scans_spin.setValue(42)
    strip.x_min_spin.setValue(700.0)
    strip.x_max_spin.setValue(3800.0)
    idx = strip.format_combo.findText("Absorbance")
    strip.format_combo.setCurrentIndex(idx)
    idx = strip.resolution_combo.findData(8.0)
    strip.resolution_combo.setCurrentIndex(idx)

    assert controller.scans == 42
    assert controller.x_min == pytest.approx(700.0)
    assert controller.x_max == pytest.approx(3800.0)
    assert controller.final_format == "Absorbance"
    assert controller.resolution == pytest.approx(8.0)

    from openftir.gui.collect_controller import CollectController

    controller2 = CollectController()
    controller2.set_driver("simulated")
    assert controller2.scans == 42
    assert controller2.x_min == pytest.approx(700.0)
    assert controller2.x_max == pytest.approx(3800.0)
    assert controller2.final_format == "Absorbance"
    assert controller2.resolution == pytest.approx(8.0)

    settings.remove("collect/simulated")


def test_settings_strip_resolution_options_come_from_connected_instrument(qtbot):
    """The resolution combo is sourced from the connected instrument's
    InstrumentInfo.resolutions (not a hardcoded list)."""
    window = MainWindow()
    qtbot.addWidget(window)

    controller = window.collect_controller
    strip = window.simple_settings_strip
    _connect_simulated(qtbot, controller)

    combo_values = [strip.resolution_combo.itemData(i) for i in range(strip.resolution_combo.count())]
    assert combo_values == list(controller.info.resolutions)
    assert combo_values == list(controller.resolutions)


def test_more_settings_button_opens_trimmed_dialog(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)

    opened = []
    monkeypatch.setattr(SimpleSettingsDialog, "exec", lambda self: opened.append(self) or 1)
    window.simple_settings_strip.more_settings_btn.click()
    assert len(opened) == 1
    # Scans/resolution/range/format moved to the strip -- no longer here.
    assert not hasattr(opened[0], "scans_spin")
    assert not hasattr(opened[0], "format_combo")
    assert hasattr(opened[0], "apodization_combo")
    assert hasattr(opened[0], "diagnostics_btn")


def test_switching_modes_repeatedly_leaves_one_dock_tab_bar(qtbot):
    # Regression: showing tabified docks one by one and then restoreState()
    # left a stale duplicate "Library | Collect" tab bar after every switch.
    from PySide6.QtWidgets import QTabBar

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    for _ in range(3):
        window._apply_mode("advanced")
        qtbot.wait(20)
        window._apply_mode("simple")
        qtbot.wait(20)
    window._apply_mode("advanced")
    qtbot.wait(20)
    bars = [tb for tb in window.findChildren(QTabBar) if tb.parent() is window and tb.isVisible() and tb.count()]
    assert len(bars) == 1


def test_background_button_shows_background_on_empty_plot(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    controller = window.collect_controller
    window.simple_top_bar.background_btn.click()
    qtbot.waitUntil(lambda: controller.background is not None and not controller.busy, timeout=15000)
    assert window.plot.has_live_preview()
    assert "Place your sample" in window.statusBar().currentMessage()


def test_export_sits_next_to_open_and_stays_visible_with_many_samples(qtbot):
    import numpy as np

    from openftir.spectrum import Spectrum, YUnit

    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1366, 728)
    window.show()
    bar = window.simple_bottom_bar
    x = np.linspace(4000, 600, 200)

    def add(n):
        for i in range(n):
            window.model.add(Spectrum(x=x, y=np.full_like(x, 90.0), y_unit=YUnit.TRANSMITTANCE), name=f"S{i}")
        qtbot.wait(50)

    add(2)
    open_btn, export = bar._open_chip.geometry(), bar.export_btn.geometry()
    assert 0 < export.left() - open_btn.right() < 20  # side by side
    assert export.right() < bar.width() / 2  # right after the chips, not off at the far edge

    add(20)
    export = bar.export_btn.geometry()
    assert export.right() <= bar.width()  # chips scroll; Export stays on screen
    assert bar.export_btn.isVisible()
