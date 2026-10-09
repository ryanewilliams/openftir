"""The main spectrum plot: a pyqtgraph view with IR conventions baked in.

Wavenumber axes are plotted high-to-low (left-to-right), and the y axis
label follows whichever spectra/preview trace are actually shown (see
``_update_axis_labels``) rather than a fixed unit.

Panning and zooming are intentionally disabled: spectroscopists compare
whole traces, not a scrolled-around fragment of one, so the view always
auto-ranges to show everything currently visible instead. The crosshair +
x/y readout stay active.

The plot is always a white background with dark axes/text -- OpenFTIR is a
light-theme-only app (see ``openftir.gui.app.apply_light_theme``) and does
not follow the OS palette.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from openftir.processing import Peak
from openftir.spectrum import XUnit, YUnit
from openftir.gui.spectrum_list import SpectrumEntry

# Lines are now drawn thicker (see the _..._PEN_WIDTH constants below); turn
# on antialiasing so they stay smooth rather than blocky.
pg.setConfigOptions(antialias=True)

_X_LABELS = {
    XUnit.WAVENUMBER: ("Wavenumber", "cm⁻¹"),
    XUnit.WAVELENGTH_UM: ("Wavelength", "µm"),
    XUnit.WAVELENGTH_NM: ("Wavelength", "nm"),
    XUnit.POINTS: ("Point", ""),
}

_Y_LABELS = {
    YUnit.ABSORBANCE: ("Absorbance", ""),
    YUnit.TRANSMITTANCE: ("Transmittance", "%"),
    YUnit.REFLECTANCE: ("Reflectance", "%"),
    YUnit.KUBELKA_MUNK: ("Kubelka-Munk", ""),
    YUnit.LOG_INV_REFLECTANCE: ("log(1/R)", ""),
    YUnit.SINGLE_BEAM: ("Single beam", "a.u."),
    YUnit.INTERFEROGRAM: ("Intensity", "a.u."),
    YUnit.ARBITRARY: ("Intensity", "a.u."),
}
#: shown when the visible spectra/preview don't all share one y unit
_MIXED_Y_LABEL = ("Intensity (mixed units)", "")

# Fixed light-theme plot colours (never follow the OS palette -- see the
# module docstring): white background, dark axes/text/crosshair.
_PLOT_BG = "#ffffff"
_PLOT_FG = "#202020"

# Vertical gap between stacked traces, as a fraction of the tallest trace's span.
_STACK_GAP_FRACTION = 1.15

_SPECTRUM_PEN_WIDTH = 2.0
_HIGHLIGHT_PEN_WIDTH = 3.0
_OVERLAY_PEN_WIDTH = 1.5


def _cosmetic_pen(*args, **kwargs):
    """``pg.mkPen`` with cosmetic width (device pixels, never scaled)."""
    pen = pg.mkPen(*args, **kwargs)
    pen.setCosmetic(True)
    return pen


class SpectrumPlotWidget(QWidget):
    """Overlay/stack plot of the currently loaded spectra."""

    #: emitted on mouse movement over the plot, in data coordinates (x, y)
    cursor_moved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stack_mode = False
        self._curves: list[pg.PlotDataItem] = []
        self._peak_items: list[pg.GraphicsObject] = []
        self._baseline_item: pg.PlotDataItem | None = None
        self._preview_item: pg.PlotDataItem | None = None
        self._preview_y_unit: YUnit | None = None
        self._highlight_row: int | None = None
        self._entries: list[SpectrumEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_item: pg.PlotItem = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.25)
        self.plot_item.invertX(True)  # IR convention: high wavenumber on the left
        self.plot_item.addLegend(
            offset=(10, 10), labelTextColor=(30, 30, 30), brush=(255, 255, 255, 220), pen=(190, 190, 190)
        )
        self.plot_item.setLabel("bottom", "Wavenumber", units="cm⁻¹")
        self.plot_item.setLabel("left", "Absorbance")
        layout.addWidget(self.plot_widget)

        # No pan/zoom: an accidentally-scrolled/zoomed view is confusing,
        # not useful, for comparing whole spectra. The view always
        # auto-ranges instead (see refresh()/show_live_preview()).
        view_box = self.plot_item.getViewBox()
        view_box.setMouseEnabled(x=False, y=False)
        view_box.setMenuEnabled(False)
        self.plot_item.setMenuEnabled(False)
        self.plot_item.hideButtons()  # the "A" auto-range corner button
        self.plot_item.enableAutoRange()

        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._hline = pg.InfiniteLine(angle=0, movable=False)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self.plot_item.addItem(self._vline, ignoreBounds=True)
        self.plot_item.addItem(self._hline, ignoreBounds=True)

        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self._apply_fixed_colors()

    # -- appearance -----------------------------------------------------------

    def _apply_fixed_colors(self) -> None:
        """White background, dark axes/text -- fixed, regardless of the OS
        theme (OpenFTIR is light-theme-only, see the module docstring)."""
        self.plot_widget.setBackground(_PLOT_BG)
        for axis_name in ("bottom", "left"):
            axis = self.plot_item.getAxis(axis_name)
            axis.setPen(pg.mkPen(_PLOT_FG))
            axis.setTextPen(pg.mkPen(_PLOT_FG))
            # Spectroscopists expect raw cm-1/absorbance values, never an
            # auto-inserted k/M SI prefix on the axis label.
            axis.enableAutoSIPrefix(False)
        self._vline.setPen(pg.mkPen(_PLOT_FG, style=Qt.DashLine))
        self._hline.setPen(pg.mkPen(_PLOT_FG, style=Qt.DashLine))

    # -- crosshair --------------------------------------------------------------

    def _on_mouse_moved(self, scene_pos) -> None:
        if not self.plot_item.sceneBoundingRect().contains(scene_pos):
            return
        point = self.plot_item.vb.mapSceneToView(scene_pos)
        x, y = point.x(), point.y()
        self._vline.setPos(x)
        self._hline.setPos(y)
        self._vline.setVisible(True)
        self._hline.setVisible(True)
        self.cursor_moved.emit(x, y)

    # -- view control -----------------------------------------------------------

    def full_scale(self) -> None:
        self.plot_item.enableAutoRange()
        self.plot_item.autoRange()

    def set_stack_mode(self, stacked: bool) -> None:
        self._stack_mode = stacked

    def stack_mode(self) -> bool:
        return self._stack_mode

    # -- axis labels ----------------------------------------------------------

    def _update_axis_labels(self) -> None:
        """Recompute both axis labels from whatever is actually on screen:
        the visible spectra, plus the live preview trace if one is shown.
        Called on add/remove/visibility changes and on preview show/clear.
        """
        visible = [e for e in self._entries if e.visible]
        basis = visible or self._entries
        if basis:
            x_unit = basis[0].spectrum.x_unit
            x_name, x_units_str = _X_LABELS.get(x_unit, (x_unit.value, ""))
            self.plot_item.setLabel("bottom", x_name, units=x_units_str)

        units = {e.spectrum.y_unit for e in visible}
        if self._preview_item is not None and self._preview_y_unit is not None:
            units.add(self._preview_y_unit)
        if not units and basis:
            units = {basis[0].spectrum.y_unit}
        if not units:
            return

        if len(units) == 1:
            unit = next(iter(units))
            y_name, y_units_str = _Y_LABELS.get(unit, (unit.value, ""))
        else:
            y_name, y_units_str = _MIXED_Y_LABEL
        self.plot_item.setLabel("left", y_name, units=y_units_str)

    # -- drawing ------------------------------------------------------------------

    def refresh(self, entries: Iterable[SpectrumEntry], highlight_row: int | None = None) -> None:
        """Redraw every visible entry. ``highlight_row`` thickens that trace."""
        self._entries = list(entries)
        self._highlight_row = highlight_row
        for curve in self._curves:
            self.plot_item.removeItem(curve)
        self._curves.clear()
        self.plot_item.legend.clear()

        visible = [(i, e) for i, e in enumerate(self._entries) if e.visible]
        self._update_axis_labels()

        # Stack offsets: stack order follows list order, tallest span sets the gap.
        offsets = [0.0] * len(visible)
        if self._stack_mode and visible:
            max_span = max(
                (float(np.ptp(e.spectrum.y)) if len(e.spectrum.y) else 1.0) for _, e in visible
            )
            gap = max_span * _STACK_GAP_FRACTION or 1.0
            for k in range(len(visible)):
                offsets[k] = k * gap

        for k, (row, entry) in enumerate(visible):
            s = entry.spectrum
            width = _HIGHLIGHT_PEN_WIDTH if row == highlight_row else _SPECTRUM_PEN_WIDTH
            pen = _cosmetic_pen(entry.color, width=width)
            y = s.y + offsets[k] if self._stack_mode else s.y
            # autoDownsample keeps multi-thousand-point spectra smooth with
            # antialiasing on. clipToView is deliberately not used here: it
            # trips an AttributeError in this pyqtgraph/Qt bindings
            # combination (PlotWidget.__getattr__ forwarding during
            # itemChange looks up 'autoRangeEnabled' on the wrong object).
            curve = self.plot_item.plot(s.x, y, pen=pen, name=entry.name, autoDownsample=True)
            self._curves.append(curve)

        self.plot_item.vb.autoRange()

    # -- peak markers -------------------------------------------------------------

    def clear_peaks(self) -> None:
        for item in self._peak_items:
            self.plot_item.removeItem(item)
        self._peak_items.clear()

    def show_peaks(self, peaks: list[Peak]) -> None:
        self.clear_peaks()
        if not peaks:
            return
        xs = [p.position for p in peaks]
        ys = [p.height for p in peaks]
        scatter = pg.ScatterPlotItem(
            x=xs, y=ys, symbol="t", size=10, brush=pg.mkBrush("#d62728"), pen=pg.mkPen(None)
        )
        self.plot_item.addItem(scatter)
        self._peak_items.append(scatter)
        for p in peaks:
            label = pg.TextItem(text=f"{p.position:.1f}", anchor=(0.5, 1.2), color="#d62728")
            label.setPos(p.position, p.height)
            self.plot_item.addItem(label)
            self._peak_items.append(label)

    # -- baseline preview -----------------------------------------------------------

    def show_baseline_preview(self, x: np.ndarray, y: np.ndarray) -> None:
        self.clear_baseline_preview()
        pen = _cosmetic_pen("#2ca02c", width=_OVERLAY_PEN_WIDTH, style=Qt.DashLine)
        self._baseline_item = self.plot_item.plot(x, y, pen=pen, name="baseline preview")

    def clear_baseline_preview(self) -> None:
        if self._baseline_item is not None:
            self.plot_item.removeItem(self._baseline_item)
            self._baseline_item = None

    # -- live collect preview --------------------------------------------------------

    def show_live_preview(
        self, x: np.ndarray, y: np.ndarray, label: str, y_unit: YUnit = YUnit.SINGLE_BEAM
    ) -> None:
        self.clear_live_preview()
        pen = _cosmetic_pen("#7f7f7f", width=_OVERLAY_PEN_WIDTH, style=Qt.DotLine)
        self._preview_item = self.plot_item.plot(x, y, pen=pen, name=label)
        self._preview_y_unit = y_unit
        self._update_axis_labels()
        self.plot_item.vb.autoRange()

    def clear_live_preview(self) -> None:
        if self._preview_item is not None:
            self.plot_item.removeItem(self._preview_item)
            self._preview_item = None
        self._preview_y_unit = None
        self._update_axis_labels()
