"""The y-axis label follows whatever spectra/preview are actually plotted,
pan/zoom is disabled, and trace lines are the thicker, antialiased pens."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor

from openftir.gui.plot import (
    _HIGHLIGHT_PEN_WIDTH,
    _OVERLAY_PEN_WIDTH,
    _SPECTRUM_PEN_WIDTH,
    SpectrumPlotWidget,
)
from openftir.gui.spectrum_list import SpectrumEntry
from openftir.spectrum import Spectrum, XUnit, YUnit


def _spectrum(y_unit: YUnit, n: int = 50) -> Spectrum:
    x = np.linspace(4000, 400, n)
    y = np.sin(x / 100.0)
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=y_unit)


def _entry(y_unit: YUnit, visible: bool = True) -> SpectrumEntry:
    return SpectrumEntry(spectrum=_spectrum(y_unit), name="s", visible=visible, color=QColor("#1f77b4"))


def test_antialiasing_enabled_for_plots(qtbot):
    SpectrumPlotWidget()  # importing/constructing applies the module-level config
    assert pg.getConfigOption("antialias") is True


def test_y_label_is_single_beam_when_only_single_beam_shown(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.refresh([_entry(YUnit.SINGLE_BEAM)])
    assert plot.plot_item.getAxis("left").labelText == "Single beam"


def test_y_label_is_absorbance_when_only_absorbance_shown(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.refresh([_entry(YUnit.ABSORBANCE)])
    assert plot.plot_item.getAxis("left").labelText == "Absorbance"


def test_y_label_is_mixed_when_units_differ(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.refresh([_entry(YUnit.ABSORBANCE), _entry(YUnit.SINGLE_BEAM)])
    assert plot.plot_item.getAxis("left").labelText == "Intensity (mixed units)"


def test_y_label_follows_live_preview_item(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.refresh([])
    x = np.linspace(4000, 400, 10)
    y = np.ones_like(x)

    plot.show_live_preview(x, y, "Background (single beam)", YUnit.SINGLE_BEAM)
    assert plot.plot_item.getAxis("left").labelText == "Single beam"

    plot.clear_live_preview()
    plot.refresh([_entry(YUnit.ABSORBANCE)])
    assert plot.plot_item.getAxis("left").labelText == "Absorbance"


def test_y_label_mixes_preview_with_visible_spectra(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.refresh([_entry(YUnit.ABSORBANCE)])
    assert plot.plot_item.getAxis("left").labelText == "Absorbance"

    x = np.linspace(4000, 400, 10)
    plot.show_live_preview(x, np.ones_like(x), "Sample (single beam)", YUnit.SINGLE_BEAM)
    assert plot.plot_item.getAxis("left").labelText == "Intensity (mixed units)"


def test_y_label_updates_on_visibility_change(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    abs_entry = _entry(YUnit.ABSORBANCE)
    sb_entry = _entry(YUnit.SINGLE_BEAM, visible=False)
    plot.refresh([abs_entry, sb_entry])
    assert plot.plot_item.getAxis("left").labelText == "Absorbance"

    sb_entry.visible = True
    plot.refresh([abs_entry, sb_entry])
    assert plot.plot_item.getAxis("left").labelText == "Intensity (mixed units)"


def test_spectrum_pen_widths_default_and_highlighted(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    entries = [_entry(YUnit.ABSORBANCE), _entry(YUnit.ABSORBANCE)]
    plot.refresh(entries, highlight_row=1)

    widths = [c.opts["pen"].widthF() for c in plot._curves]
    assert widths[0] == _SPECTRUM_PEN_WIDTH
    assert widths[1] == _HIGHLIGHT_PEN_WIDTH > _SPECTRUM_PEN_WIDTH
    assert all(c.opts["pen"].isCosmetic() for c in plot._curves)


def test_preview_and_baseline_pens_are_thin_dashed(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    x = np.linspace(4000, 400, 10)
    y = np.ones_like(x)

    plot.show_live_preview(x, y, "preview", YUnit.SINGLE_BEAM)
    preview_pen = plot._preview_item.opts["pen"]
    assert preview_pen.widthF() == _OVERLAY_PEN_WIDTH
    assert preview_pen.isCosmetic()

    plot.show_baseline_preview(x, y)
    baseline_pen = plot._baseline_item.opts["pen"]
    assert baseline_pen.widthF() == _OVERLAY_PEN_WIDTH
    assert baseline_pen.isCosmetic()


def test_view_box_mouse_and_menu_disabled(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    vb = plot.plot_item.getViewBox()
    assert vb.state["mouseEnabled"] == [False, False]
    assert plot.plot_item.buttonsHidden is True


# -- automatic peak labelling -------------------------------------------------------------


def _labelled_transmittance_entry() -> SpectrumEntry:
    """A %T spectrum with one deep band above the default 1500 cm-1 cutoff
    and one deep band below it, so auto-labelling should only mark the
    first one."""
    x = np.linspace(4000.0, 400.0, 1800)
    y = 100.0 - 60.0 / (1.0 + ((x - 2917.0) / 10.0) ** 2) - 60.0 / (1.0 + ((x - 1300.0) / 10.0) ** 2)
    s = Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.TRANSMITTANCE)
    return SpectrumEntry(spectrum=s, name="t", visible=True, color=QColor("#1f77b4"))


def test_auto_label_toggle_adds_and_removes_labels(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    entry = _labelled_transmittance_entry()

    plot.set_auto_label_enabled(True)
    plot.refresh([entry])
    assert len(plot._auto_label_items) > 0

    plot.set_auto_label_enabled(False)
    assert plot._auto_label_items == []

    plot.set_auto_label_enabled(True)
    assert len(plot._auto_label_items) > 0


def test_auto_label_none_for_single_beam_spectrum(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    plot.set_auto_label_enabled(True)
    plot.refresh([_entry(YUnit.SINGLE_BEAM)])
    assert plot._auto_label_items == []


def test_auto_label_respects_cutoff_wavenumber(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    entry = _labelled_transmittance_entry()
    plot.set_auto_label_enabled(True)
    plot.refresh([entry])

    # Only the TextItem labels (not the ScatterPlotItem markers) carry text;
    # every one of them must be above the default 1500 cm-1 cutoff.
    labelled_positions = [item.pos().x() for item in plot._auto_label_items if isinstance(item, pg.TextItem)]
    assert labelled_positions, "expected at least one label"
    assert all(pos > 1500.0 for pos in labelled_positions)

    plot.set_auto_label_params(min_wavenumber=1000.0)
    labelled_positions_lower_cutoff = [
        item.pos().x() for item in plot._auto_label_items if isinstance(item, pg.TextItem)
    ]
    assert len(labelled_positions_lower_cutoff) > len(labelled_positions)
