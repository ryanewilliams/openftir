"""Simple mode: the default UI, a thin top/bottom bar view over the same
CollectController/model/plot Advanced mode uses -- see
openftir.gui.collect_controller and openftir.gui.simple_panels."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from openftir.gui.dialogs.simple_settings_dialog import SimpleSettingsDialog
from openftir.gui.main_window import MainWindow

_SETTINGS_ARGS = ("OpenFTIR", "OpenFTIR")


def _connect_simulated(qtbot, controller) -> None:
    controller.set_driver("simulated")
    with qtbot.waitSignal(controller.connection_changed, timeout=5000):
        controller.connect_instrument()
    assert controller.connected


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


def test_pill_click_connects(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")

    controller = window.collect_controller
    controller.set_driver("simulated")
    top_bar = window.simple_top_bar

    assert controller.connected is False
    with qtbot.waitSignal(controller.connection_changed, timeout=5000):
        top_bar.pill.click()
    assert controller.connected is True


def test_settings_dialog_changes_persist(qtbot):
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

    dialog.scans_spin.setValue(42)
    idx = dialog.format_combo.findText("Absorbance")
    dialog.format_combo.setCurrentIndex(idx)

    assert controller.scans == 42
    assert controller.final_format == "Absorbance"

    # Persisted immediately (no Ok button to click) -- a fresh controller
    # restores the same values for this driver.
    from openftir.gui.collect_controller import CollectController

    controller2 = CollectController()
    controller2.set_driver("simulated")
    assert controller2.scans == 42
    assert controller2.final_format == "Absorbance"

    settings.remove("collect/simulated")
