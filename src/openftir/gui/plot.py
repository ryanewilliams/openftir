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
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QVBoxLayout, QWidget

from openftir.processing import Peak, auto_label_peaks
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

_SPECTRUM_PEN_WIDTH = 1.0
_HIGHLIGHT_PEN_WIDTH = 1.5
_OVERLAY_PEN_WIDTH = 1.0

# -- auto peak labelling (View -> Auto-label peaks) --------------------------
_AUTO_LABEL_FONT_PT = 8
_AUTO_LABEL_MARKER_SIZE = 5
#: how far (as a fraction of the visible y span) a label is nudged away from
#: its dip/peak -- below a %T dip, above an absorbance peak.
_AUTO_LABEL_Y_OFFSET_FRACTION = 0.012
#: extra room kept below the lowest dip (above the highest peak) for labels
_AUTO_LABEL_HEADROOM_FRACTION = 0.07
#: minimum x separation (as a fraction of the visible x span) between two
#: placed labels -- the "simple greedy" overlap avoidance. Two labels only
#: count as conflicting if they are close in BOTH x and y (see
#: _AUTO_LABEL_MIN_Y_GAP_FRACTION): close in x but far apart in y means
#: they belong to different, clearly-separated traces (e.g. stacked
#: spectra, or two overlaid spectra with a close but not coincident band).
_AUTO_LABEL_MIN_X_GAP_FRACTION = 0.016
_AUTO_LABEL_MIN_Y_GAP_FRACTION = 0.06
_AUTO_LABEL_X_MARGIN_FRACTION = 0.015
_AUTO_LABEL_Y_MARGIN_FRACTION = 0.03

_DEFAULT_AUTO_LABEL_PARAMS: dict = {
    "max_transmittance": 85.0,
    "min_wavenumber": 1500.0,
    "min_prominence_t": 2.0,
    "min_distance_cm": 15.0,
}


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
        self._auto_label_enabled = True
        self._auto_label_params: dict = dict(_DEFAULT_AUTO_LABEL_PARAMS)
        self._auto_label_items: list[pg.GraphicsObject] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_item: pg.PlotItem = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.25)
        self.plot_item.invertX(True)  # IR convention: high wavenumber on the left
        self.plot_item.addLegend(
            offset=(-10, 10), labelTextColor=(30, 30, 30), brush=(255, 255, 255, 220), pen=(190, 190, 190)
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
        self.clear_auto_labels()

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

        # (position, value, down, color) candidates for auto-labelling,
        # gathered across every visible trace and placed in one pass below
        # so overlap avoidance works across spectra, not just within one.
        label_candidates: list[tuple[float, float, bool, QColor]] = []

        for k, (row, entry) in enumerate(visible):
            s = entry.spectrum
            width = _HIGHLIGHT_PEN_WIDTH if row == highlight_row else _SPECTRUM_PEN_WIDTH
            pen = _cosmetic_pen(entry.color, width=width)
            offset = offsets[k] if self._stack_mode else 0.0
            y = s.y + offset if self._stack_mode else s.y
            # autoDownsample keeps multi-thousand-point spectra smooth with
            # antialiasing on. clipToView is deliberately not used here: it
            # trips an AttributeError in this pyqtgraph/Qt bindings
            # combination (PlotWidget.__getattr__ forwarding during
            # itemChange looks up 'autoRangeEnabled' on the wrong object).
            curve = self.plot_item.plot(s.x, y, pen=pen, name=entry.name, autoDownsample=True)
            self._curves.append(curve)

            if self._auto_label_enabled:
                try:
                    peaks = auto_label_peaks(s, **self._auto_label_params)
                except ValueError:
                    peaks = []  # single-beam/interferogram/etc. -- never labelled
                down = s.y_unit in (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE)
                for p in peaks:
                    label_candidates.append((p.position, p.height + offset, down, entry.color))

        if label_candidates:
            self._reserve_label_headroom(visible, offsets, label_candidates)
        self.plot_item.vb.autoRange()
        if label_candidates:
            self._place_auto_labels(label_candidates)

    def _reserve_label_headroom(self, visible, offsets, label_candidates) -> None:
        """Extend the autoranged view below (for %T dips) or above (for
        absorbance peaks) the data so labels under a dip that bottoms out
        near the plot edge (e.g. a saturated band at 0 %T) aren't clipped.
        Done with an invisible point that counts towards the bounds."""
        ys = [
            e.spectrum.y + (offsets[k] if self._stack_mode else 0.0)
            for k, (_, e) in enumerate(visible)
            if len(e.spectrum.y)
        ]
        if not ys:
            return
        y_min = min(float(np.nanmin(y)) for y in ys)
        y_max = max(float(np.nanmax(y)) for y in ys)
        pad = (y_max - y_min or 1.0) * _AUTO_LABEL_HEADROOM_FRACTION
        x_mid = float(np.mean([c[0] for c in label_candidates]))
        pts = []
        if any(down for _, _, down, _ in label_candidates):
            pts.append(y_min - pad)
        if any(not down for _, _, down, _ in label_candidates):
            pts.append(y_max + pad)
        spacer = pg.ScatterPlotItem(
            x=[x_mid] * len(pts), y=pts, size=1, brush=pg.mkBrush(0, 0, 0, 0), pen=pg.mkPen(None)
        )
        self.plot_item.addItem(spacer)  # deliberately NOT ignoreBounds
        self._auto_label_items.append(spacer)

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

    # -- automatic peak labelling (View -> Auto-label peaks) ------------------------

    def set_auto_label_enabled(self, enabled: bool) -> None:
        self._auto_label_enabled = enabled
        self.refresh(self._entries, self._highlight_row)

    def set_auto_label_params(
        self,
        max_transmittance: float | None = None,
        min_wavenumber: float | None = None,
        min_prominence_t: float | None = None,
        min_distance_cm: float | None = None,
    ) -> None:
        for key, value in (
            ("max_transmittance", max_transmittance),
            ("min_wavenumber", min_wavenumber),
            ("min_prominence_t", min_prominence_t),
            ("min_distance_cm", min_distance_cm),
        ):
            if value is not None:
                self._auto_label_params[key] = value
        self.refresh(self._entries, self._highlight_row)

    def clear_auto_labels(self) -> None:
        for item in self._auto_label_items:
            self.plot_item.removeItem(item)
        self._auto_label_items.clear()

    def _place_auto_labels(self, candidates: list[tuple[float, float, bool, QColor]]) -> None:
        """Greedily place a marker + integer wavenumber label per candidate.

        Candidates are processed strongest (deepest dip / tallest peak)
        first, skipping any whose position falls within
        ``_AUTO_LABEL_MIN_X_GAP_FRACTION`` of an already-placed label -- a
        simple way to avoid two labels overlapping when their peaks sit
        close together, while keeping the more significant band of the
        pair rather than whichever happens to come first left-to-right.
        Label positions are clamped inside the current view so they never
        overlap the plot frame, and the items are added with
        ``ignoreBounds=True`` so they never affect autorange.
        """
        x_range, y_range = self.plot_item.vb.viewRange()
        x_lo, x_hi = sorted(x_range)
        y_lo, y_hi = sorted(y_range)
        x_span = (x_hi - x_lo) or 1.0
        y_span = (y_hi - y_lo) or 1.0
        y_offset = y_span * _AUTO_LABEL_Y_OFFSET_FRACTION
        min_x_gap = x_span * _AUTO_LABEL_MIN_X_GAP_FRACTION
        min_y_gap = y_span * _AUTO_LABEL_MIN_Y_GAP_FRACTION
        x_margin = x_span * _AUTO_LABEL_X_MARGIN_FRACTION
        y_margin = y_span * _AUTO_LABEL_Y_MARGIN_FRACTION

        font = QFont()
        font.setPointSize(_AUTO_LABEL_FONT_PT)

        # Depth/height below-100 or above-0 as a proxy for how significant a
        # band is, so a conflict between two close peaks keeps the deeper
        # (more important) one rather than whichever sorts first in x.
        def _significance(c: tuple[float, float, bool, QColor]) -> float:
            _position, value, down, _color = c
            return (100.0 - value) if down else value

        placed: list[tuple[float, float]] = []
        ordered = sorted(candidates, key=lambda c: (-_significance(c), c[0]))
        for position, value, down, color in ordered:
            label_y_raw = value - y_offset if down else value + y_offset
            if any(
                abs(position - px) < min_x_gap and abs(label_y_raw - py) < min_y_gap
                for px, py in placed
            ):
                continue
            placed.append((position, label_y_raw))

            label_y = min(max(label_y_raw, y_lo + y_margin), y_hi - y_margin)
            label_x = min(max(position, x_lo + x_margin), x_hi - x_margin)

            marker = pg.ScatterPlotItem(
                x=[position], y=[value], symbol="o", size=_AUTO_LABEL_MARKER_SIZE,
                brush=pg.mkBrush(color), pen=pg.mkPen(None),
            )
            self.plot_item.addItem(marker, ignoreBounds=True)
            self._auto_label_items.append(marker)

            text = pg.TextItem(
                text=str(int(round(position))),
                color=color,
                anchor=(0.5, 0.0) if down else (0.5, 1.0),
            )
            text.setFont(font)
            text.setPos(label_x, label_y)
            self.plot_item.addItem(text, ignoreBounds=True)
            self._auto_label_items.append(text)

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
