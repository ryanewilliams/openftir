"""Manual peak picking: click-to-add/remove on the plot (both Simple and
Advanced mode, via the shared ``pick_peaks_action``/``clear_manual_peaks_
action`` -- see openftir.gui.main_window.MainWindow and
openftir.gui.plot.SpectrumPlotWidget.set_pick_mode/pick_requested)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest

from openftir import io
from openftir.gui.main_window import MainWindow
from openftir.spectrum import Spectrum, XUnit, YUnit


def _use_large_screen(monkeypatch) -> QRect:
    available = QRect(0, 0, 2000, 1400)
    monkeypatch.setattr(QScreen, "availableGeometry", lambda self: available)
    return available


def _transmittance_dip(center: float = 1500.0, depth: float = 50.0, n: int = 2001) -> Spectrum:
    """A clean %T spectrum with one unambiguous minimum at ``center`` --
    the grid is built so ``center`` lands exactly on a sample point."""
    x = np.linspace(center - 500.0, center + 500.0, n)
    y = 100.0 - depth * np.exp(-((x - center) ** 2) / (2 * 40.0**2))
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.TRANSMITTANCE, title="dip")


def _click_data_point(qtbot, window, x: float, y: float) -> None:
    """Simulate a real left click on the plot at data coordinates (x, y),
    going through the plot's own view<->scene<->viewport coordinate
    mapping -- exactly what a real mouse click produces."""
    plot = window.plot
    scene_pos = plot.plot_item.vb.mapViewToScene(QPointF(x, y))
    viewport_pos = plot.plot_widget.mapFromScene(scene_pos)
    qtbot.mouseClick(plot.plot_widget.viewport(), Qt.LeftButton, pos=viewport_pos)


def _prepare_window(qtbot, monkeypatch, spectrum: Spectrum):
    _use_large_screen(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window._apply_mode("simple")
    row = window.model.add(spectrum, name="dip")
    window.list_view.setCurrentIndex(window.model.index(row))
    window.resize(1400, 900)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(50)
    return window


def test_pick_mode_click_snaps_to_true_minimum(qtbot, monkeypatch):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    true_min_idx = int(np.argmin(spectrum.y))
    true_min_x = float(spectrum.x[true_min_idx])

    window.pick_peaks_action.setChecked(True)
    assert window.plot.pick_mode() is True

    _click_data_point(qtbot, window, true_min_x, float(spectrum.y[true_min_idx]))

    entry = window.model.entry_at(0)
    assert len(entry.manual_peaks) == 1
    spacing = abs(spectrum.x[1] - spectrum.x[0])
    assert abs(entry.manual_peaks[0] - true_min_x) <= spacing + 1e-6


def test_pick_mode_second_click_near_existing_peak_removes_it(qtbot, monkeypatch):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    true_min_idx = int(np.argmin(spectrum.y))
    true_min_x = float(spectrum.x[true_min_idx])
    true_min_y = float(spectrum.y[true_min_idx])

    window.pick_peaks_action.setChecked(True)
    _click_data_point(qtbot, window, true_min_x, true_min_y)
    assert len(window.model.entry_at(0).manual_peaks) == 1

    _click_data_point(qtbot, window, true_min_x, true_min_y)
    assert window.model.entry_at(0).manual_peaks == []


def test_esc_exits_pick_mode(qtbot, monkeypatch):
    window = _prepare_window(qtbot, monkeypatch, _transmittance_dip())
    window.activateWindow()
    qtbot.waitActive(window)

    window.pick_peaks_action.setChecked(True)
    assert window.plot.pick_mode() is True

    QTest.keyClick(window, Qt.Key_Escape)

    assert window.pick_peaks_action.isChecked() is False
    assert window.plot.pick_mode() is False


def test_manual_peaks_survive_ofjson_round_trip(qtbot, monkeypatch, tmp_path):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    window.model.set_manual_peaks(0, [1493.0, 698.0])

    out_path = tmp_path / "manual_peaks.ofjson"
    entry = window.model.entry_at(0)
    io.save([entry.spectrum], str(out_path))

    window2 = MainWindow()
    qtbot.addWidget(window2)
    window2._open_paths([str(out_path)])

    assert window2.model.rowCount() == 1
    loaded_entry = window2.model.entry_at(0)
    assert sorted(loaded_entry.manual_peaks) == [698.0, 1493.0]
    assert sorted(loaded_entry.spectrum.meta["manual_peaks"]) == [698.0, 1493.0]


def test_peaks_table_shows_manual_type(qtbot, monkeypatch):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    window.model.set_manual_peaks(0, [1500.0])
    window._refresh_peaks_panel()

    rows = window.simple_peaks_panel._rows
    assert any(kind == "manual" and peak.position == pytest.approx(1500.0) for peak, kind in rows)
    advanced_rows = window.peaks_panel._rows
    assert any(kind == "manual" and peak.position == pytest.approx(1500.0) for peak, kind in advanced_rows)


def test_manual_peaks_shown_when_auto_labels_off(qtbot, monkeypatch):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    window.auto_label_action.setChecked(False)
    window.model.set_manual_peaks(0, [1500.0])

    assert len(window.plot._auto_label_items) > 0  # the manual marker + label

    rows = window.simple_peaks_panel._rows
    assert any(kind == "manual" for _peak, kind in rows)


def test_clear_picks_removes_all_manual_peaks(qtbot, monkeypatch):
    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)

    window.model.set_manual_peaks(0, [1500.0, 1400.0])
    window.clear_manual_peaks_action.trigger()
    assert window.model.entry_at(0).manual_peaks == []


def test_processing_action_does_not_copy_manual_peaks(qtbot, monkeypatch):
    from openftir import processing

    spectrum = _transmittance_dip(center=1500.0)
    window = _prepare_window(qtbot, monkeypatch, spectrum)
    window.model.set_manual_peaks(0, [1500.0])

    new_row = window._apply(lambda s: processing.savgol(s, window=11, order=2), "smoothed")
    assert new_row is not None
    new_entry = window.model.entry_at(new_row)
    assert new_entry.manual_peaks == []
    assert "manual_peaks" not in new_entry.spectrum.meta
