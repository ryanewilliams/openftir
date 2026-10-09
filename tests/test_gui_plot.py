"""The y-axis label follows whatever spectra/preview are actually plotted,
pan/zoom is disabled, and trace lines are the thicker, antialiased pens."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor

from openftir.gui.plot import SpectrumPlotWidget
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
    assert widths[0] == 2.0
    assert widths[1] == 3.0
    assert all(c.opts["pen"].isCosmetic() for c in plot._curves)


def test_preview_and_baseline_pens_are_thin_dashed(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    x = np.linspace(4000, 400, 10)
    y = np.ones_like(x)

    plot.show_live_preview(x, y, "preview", YUnit.SINGLE_BEAM)
    preview_pen = plot._preview_item.opts["pen"]
    assert preview_pen.widthF() == 1.5
    assert preview_pen.isCosmetic()

    plot.show_baseline_preview(x, y)
    baseline_pen = plot._baseline_item.opts["pen"]
    assert baseline_pen.widthF() == 1.5
    assert baseline_pen.isCosmetic()


def test_view_box_mouse_and_menu_disabled(qtbot):
    plot = SpectrumPlotWidget()
    qtbot.addWidget(plot)
    vb = plot.plot_item.getViewBox()
    assert vb.state["mouseEnabled"] == [False, False]
    assert plot.plot_item.buttonsHidden is True
