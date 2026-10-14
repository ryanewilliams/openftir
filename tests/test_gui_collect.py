"""Collect panel: background -> sample collection through the worker thread
produces a background-ratioed spectrum, and Cancel actually stops a run."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog

from openftir.gui import file_dialogs
from openftir.gui.collect_panel import CollectPanel
from openftir.spectrum import YUnit


def _connect_simulated(qtbot, panel: CollectPanel) -> None:
    idx = panel.driver_combo.findText("simulated")
    assert idx >= 0, "simulated driver should be registered"
    panel.driver_combo.setCurrentIndex(idx)
    panel.connect_btn.click()
    qtbot.waitUntil(lambda: panel.status_label.text().startswith("Connected"), timeout=5000)


def test_background_then_sample_collect_ratios(qtbot, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Test Sample", True)))

    panel = CollectPanel()
    qtbot.addWidget(panel)
    _connect_simulated(qtbot, panel)

    panel.scans_spin.setValue(2)

    with qtbot.waitSignal(panel.preview_ready, timeout=10000):
        panel.bg_btn.click()
    qtbot.waitUntil(lambda: panel._background is not None, timeout=10000)

    with qtbot.waitSignal(panel.spectrum_collected, timeout=10000) as blocker:
        panel.sample_btn.click()

    spectrum, name = blocker.args
    assert name == "Test Sample"
    assert spectrum.y_unit in (YUnit.ABSORBANCE, YUnit.TRANSMITTANCE)
    assert len(spectrum) > 0
    # The collection time is recorded (shown as the PDF report's Date).
    assert spectrum.acquired is not None


def test_cancel_stops_collection(qtbot):
    panel = CollectPanel()
    qtbot.addWidget(panel)
    _connect_simulated(qtbot, panel)

    # Slow the (virtual) instrument down so there is time to cancel mid-scan.
    panel._instrument.scan_time_s = 0.2
    panel.scans_spin.setValue(50)

    messages = []
    panel.status_message.connect(messages.append)

    panel.bg_btn.click()
    qtbot.waitUntil(lambda: panel.cancel_btn.isEnabled(), timeout=5000)
    qtbot.wait(150)
    panel.cancel_btn.click()

    qtbot.waitUntil(lambda: "Collection cancelled" in messages, timeout=10000)
    assert panel._background is None


def test_simulated_sample_row_hidden_for_non_simulated_driver(qtbot):
    """The 'Simulated sample:' label + combo should only show up for the
    'simulated' driver -- a dangling label with no visible control next to
    it (as seen with the real 'summit' driver) looks broken."""
    panel = CollectPanel()
    qtbot.addWidget(panel)
    panel.show()  # isVisible() is only meaningful once the widget is shown

    idx = panel.driver_combo.findText("summit")
    assert idx >= 0, "summit driver should be registered"
    panel.driver_combo.setCurrentIndex(idx)
    assert panel.driver_form.isRowVisible(panel.sim_sample_combo) is False
    assert panel.sim_sample_combo.isVisible() is False

    idx = panel.driver_combo.findText("simulated")
    panel.driver_combo.setCurrentIndex(idx)
    assert panel.driver_form.isRowVisible(panel.sim_sample_combo) is True
    assert panel.sim_sample_combo.isVisible() is True


def test_background_is_added_to_spectrum_list_and_save_disabled_without_one(qtbot):
    panel = CollectPanel()
    qtbot.addWidget(panel)
    _connect_simulated(qtbot, panel)
    panel.scans_spin.setValue(2)

    assert panel.save_bg_btn.isEnabled() is False

    collected = []
    panel.spectrum_collected.connect(lambda s, n: collected.append((s, n)))

    with qtbot.waitSignal(panel.preview_ready, timeout=10000):
        panel.bg_btn.click()
    qtbot.waitUntil(lambda: panel._background is not None, timeout=10000)

    assert len(collected) == 1
    spectrum, name = collected[0]
    assert name.startswith("Background ")
    assert spectrum is panel._background
    assert panel.save_bg_btn.isEnabled() is True


def test_save_and_load_background_round_trip(qtbot, tmp_path, monkeypatch):
    panel = CollectPanel()
    qtbot.addWidget(panel)
    _connect_simulated(qtbot, panel)
    panel.scans_spin.setValue(2)

    with qtbot.waitSignal(panel.preview_ready, timeout=10000):
        panel.bg_btn.click()
    qtbot.waitUntil(lambda: panel._background is not None, timeout=10000)

    save_path = tmp_path / "bg.ofjson"
    monkeypatch.setattr(file_dialogs, "get_save_path", lambda *a, **k: str(save_path))
    panel._on_save_background()
    assert save_path.exists()

    panel2 = CollectPanel()
    qtbot.addWidget(panel2)
    loaded = []
    panel2.spectrum_collected.connect(lambda s, n: loaded.append((s, n)))
    monkeypatch.setattr(file_dialogs, "get_open_paths", lambda *a, **k: [str(save_path)])

    panel2._on_load_background()

    assert panel2._background is not None
    assert panel2.save_bg_btn.isEnabled() is True
    assert len(loaded) == 1
    loaded_spectrum, loaded_name = loaded[0]
    assert loaded_name.startswith("Background ")
    assert len(loaded_spectrum) == len(panel._background)


def test_default_final_format_is_percent_transmittance_on_fresh_settings(qtbot):
    """ATR-FTIR users normally work in %T -- a never-used driver (or a
    fresh install) should default to %Transmittance, not Absorbance."""
    settings = QSettings("OpenFTIR", "OpenFTIR")
    settings.remove("collect/simulated")

    panel = CollectPanel()
    qtbot.addWidget(panel)
    idx = panel.driver_combo.findText("simulated")
    panel.driver_combo.setCurrentIndex(idx)

    assert panel.format_combo.currentText() == "%Transmittance"


def test_default_final_format_ignores_pre_change_stored_absorbance(qtbot):
    """A value saved under the old 'format' settings key (from before %T
    became the default) must not resurrect Absorbance as the default."""
    settings = QSettings("OpenFTIR", "OpenFTIR")
    settings.setValue("collect/simulated/format", "Absorbance")
    settings.remove("collect/simulated/format_v2")

    panel = CollectPanel()
    qtbot.addWidget(panel)
    idx = panel.driver_combo.findText("simulated")
    panel.driver_combo.setCurrentIndex(idx)

    assert panel.format_combo.currentText() == "%Transmittance"
    settings.remove("collect/simulated/format")


def test_default_scans_is_16_for_a_driver_never_used_before(qtbot):
    settings = QSettings("OpenFTIR", "OpenFTIR")
    settings.remove("collect/simulated")

    panel = CollectPanel()
    qtbot.addWidget(panel)
    idx = panel.driver_combo.findText("simulated")
    panel.driver_combo.setCurrentIndex(idx)

    assert panel.scans_spin.value() == 16


def test_remembers_last_used_settings_per_driver(qtbot):
    settings = QSettings("OpenFTIR", "OpenFTIR")
    settings.remove("collect/simulated")

    panel = CollectPanel()
    qtbot.addWidget(panel)
    _connect_simulated(qtbot, panel)

    panel.scans_spin.setValue(20)
    panel.x_min_spin.setValue(650.0)
    panel.x_max_spin.setValue(3900.0)
    panel.apodization_combo.setCurrentText("boxcar")
    panel.format_combo.setCurrentText("%Transmittance")

    with qtbot.waitSignal(panel.preview_ready, timeout=10000):
        panel.bg_btn.click()
    qtbot.waitUntil(lambda: panel._background is not None, timeout=10000)

    panel2 = CollectPanel()
    qtbot.addWidget(panel2)
    idx2 = panel2.driver_combo.findText("simulated")
    panel2.driver_combo.setCurrentIndex(idx2)

    assert panel2.scans_spin.value() == 20
    assert panel2.x_min_spin.value() == pytest.approx(650.0)
    assert panel2.x_max_spin.value() == pytest.approx(3900.0)
    assert panel2.apodization_combo.currentText() == "boxcar"
    assert panel2.format_combo.currentText() == "%Transmittance"

    settings.remove("collect/simulated")


def test_driver_that_needs_a_file_asks_for_it(qtbot, tmp_path, monkeypatch):
    """The Varian needs its firmware file: connecting without one asks for
    it (and doesn't try to connect); a chosen file is remembered."""
    from openftir.gui.collect_controller import CollectController
    from openftir.instruments.varian.fake import synthetic_firmware
    from openftir.instruments.varian.protocol import FIRMWARE_ENV

    monkeypatch.delenv(FIRMWARE_ENV, raising=False)
    controller = CollectController()
    controller.set_driver("varian")
    controller._settings.remove("collect/varian/file/firmware")
    try:
        assert controller.apodization == "norton-beer-medium"   # the Varian's default

        with qtbot.waitSignal(controller.file_needed, timeout=2000) as blocker:
            controller.connect_instrument()
        driver_id, key, description, name_filter, reason = blocker.args
        assert (driver_id, key) == ("varian", "firmware")
        assert "firmware" in description and "*.hex" in name_filter
        assert not controller.connected

        fw = tmp_path / "bench.hex"
        fw.write_bytes(synthetic_firmware())
        controller.set_driver_file("firmware", str(fw))
        assert controller._driver_files("varian") == {"firmware": str(fw)}

        # A file that turns out to be unusable is forgotten and asked for again.
        fw.write_bytes(b"not firmware" * 200)
        with qtbot.waitSignal(controller.file_needed, timeout=2000):
            controller.connect_instrument()
        assert controller.driver_file("firmware") == ""
    finally:
        controller._settings.remove("collect/varian")
        controller.set_driver("simulated")
