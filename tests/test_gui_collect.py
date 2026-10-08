"""Collect panel: background -> sample collection through the worker thread
produces a background-ratioed spectrum, and Cancel actually stops a run."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QInputDialog

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
