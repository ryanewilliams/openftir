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
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from openftir.label_layout import LabelRequest, place_labels
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
#: extra room kept below the lowest dip (above the highest peak) for labels
_AUTO_LABEL_HEADROOM_FRACTION = 0.07
#: leader lines from a moved label back to its peak
_LEADER_COLOR = (110, 110, 110)

_DEFAULT_AUTO_LABEL_PARAMS: dict = {
    "max_transmittance": 85.0,
    "min_wavenumber": 1500.0,
    "min_prominence_t": 2.0,
    "min_distance_cm": 15.0,
}

#: %T/%R-like units: manual/auto markers point down into a dip; absorbance
#: points up out of a peak. Anything else (single beam, interferogram, ...)
#: never gets a manual-peak marker -- see SpectrumPlotWidget.refresh.
_DOWN_UNITS = (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE)
_UP_UNITS = (YUnit.ABSORBANCE,)

#: manual peaks are drawn slightly larger/bolder than auto-labelled ones --
#: they're a deliberate pick, not an algorithm's guess (see refresh()).
_MANUAL_LABEL_MARKER_SIZE = 8
#: auto labels within this many cm-1 of a manual pick are dropped (the
#: manual one wins) -- see refresh().
_MANUAL_LABEL_COLLISION_CM = 4.0


def _cosmetic_pen(*args, **kwargs):
    """``pg.mkPen`` with cosmetic width (device pixels, never scaled)."""
    pen = pg.mkPen(*args, **kwargs)
    pen.setCosmetic(True)
    return pen


def _nearest_value(x: np.ndarray, y: np.ndarray, position: float) -> float | None:
    """``y`` at the data point in ``x`` closest to ``position`` -- used to
    look up a manual peak's plotted height from its stored position alone."""
    if x.size == 0:
        return None
    idx = int(np.argmin(np.abs(x - position)))
    return float(y[idx])


class SpectrumPlotWidget(QWidget):
    """Overlay/stack plot of the currently loaded spectra."""

    #: emitted on mouse movement over the plot, in data coordinates (x, y)
    cursor_moved = Signal(float, float)
    #: emitted on a left click while pick-peaks mode is on, in data
    #: coordinates (x, y) -- see set_pick_mode()/MainWindow._on_plot_pick.
    pick_requested = Signal(float, float)

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
        # Markers, labels and leader lines (a subset of _auto_label_items):
        # re-placed whenever the plot is resized, since layout is in pixels.
        self._label_items: list[pg.GraphicsObject] = []
        self._label_candidates: list[tuple[float, float, bool, QColor, bool]] = []
        self._label_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._relabel_timer = QTimer(self)
        self._relabel_timer.setSingleShot(True)
        self._relabel_timer.setInterval(40)
        self._relabel_timer.timeout.connect(self._place_auto_labels)
        self._pick_mode = False

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
        # Labels are laid out in pixels: redo them when the plot changes size.
        view_box.sigResized.connect(self._schedule_relabel)
        view_box.sigRangeChanged.connect(self._schedule_relabel)

        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._hline = pg.InfiniteLine(angle=0, movable=False)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self.plot_item.addItem(self._vline, ignoreBounds=True)
        self.plot_item.addItem(self._hline, ignoreBounds=True)

        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_scene_clicked)

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

    # -- pick-peaks mode ----------------------------------------------------------

    def set_pick_mode(self, enabled: bool) -> None:
        """Toggle manual peak-picking mode: while on, the cursor becomes a
        crosshair and a left click emits ``pick_requested`` in data
        coordinates (see MainWindow._on_plot_pick for the actual snap/add/
        remove logic -- this widget only handles the mouse/cursor side)."""
        self._pick_mode = enabled
        self.plot_widget.viewport().setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def pick_mode(self) -> bool:
        return self._pick_mode

    def pixel_size(self) -> tuple[float, float]:
        """``(x, y)`` size of one screen pixel in data units, for converting
        a pixel tolerance (e.g. "within 12 px") to a data-space window."""
        return self.plot_item.vb.viewPixelSize()

    def _on_scene_clicked(self, event) -> None:
        if not self._pick_mode or event.button() != Qt.LeftButton:
            return
        scene_pos = event.scenePos()
        if not self.plot_item.sceneBoundingRect().contains(scene_pos):
            return
        point = self.plot_item.vb.mapSceneToView(scene_pos)
        self.pick_requested.emit(point.x(), point.y())

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
            units = {YUnit.TRANSMITTANCE}  # empty plot: the app's default format

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

        # (position, value, down, color, manual) candidates for labelling,
        # gathered across every visible trace and placed in one pass below
        # so overlap avoidance works across spectra, not just within one.
        # Manual picks are always included (even with auto-labels off);
        # auto candidates are dropped when they coincide with a manual one
        # -- see _place_auto_labels for the priority rule.
        label_candidates: list[tuple[float, float, bool, QColor, bool]] = []
        label_curves: list[tuple[np.ndarray, np.ndarray]] = []

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
            label_curves.append((s.x, y))

            down = s.y_unit in _DOWN_UNITS
            manual_positions = list(entry.manual_peaks) if s.y_unit in (*_DOWN_UNITS, *_UP_UNITS) else []
            for position in manual_positions:
                value = _nearest_value(s.x, s.y, position)
                if value is None:
                    continue
                label_candidates.append((position, value + offset, down, entry.color, True))

            if self._auto_label_enabled:
                try:
                    peaks = auto_label_peaks(s, **self._auto_label_params)
                except ValueError:
                    peaks = []  # single-beam/interferogram/etc. -- never labelled
                for p in peaks:
                    if any(abs(p.position - m) <= _MANUAL_LABEL_COLLISION_CM for m in manual_positions):
                        continue  # a manual pick already marks this band
                    label_candidates.append((p.position, p.height + offset, down, entry.color, False))

        # legend.clear() above also dropped the preview trace's entry.
        if self._preview_item is not None and self.plot_item.legend is not None:
            self.plot_item.legend.addItem(self._preview_item, self._preview_item.name())
        self._update_legend_visibility()

        if label_candidates:
            self._reserve_label_headroom(visible, offsets, label_candidates)
        if visible or self._preview_item is not None:
            self.plot_item.vb.autoRange()
        else:
            # Empty plot: show the usual mid-IR window rather than pyqtgraph's
            # meaningless default 0-1 range.
            self.plot_item.vb.setRange(xRange=(600, 4000), yRange=(0, 100), padding=0.02)
        self._label_candidates = label_candidates
        self._label_curves = label_curves
        if label_candidates:
            self._place_auto_labels()

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
        if any(down for _, _, down, _, _ in label_candidates):
            pts.append(y_min - pad)
        if any(not down for _, _, down, _, _ in label_candidates):
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

    def render_export(self, path: str, width: int = 1600, height: int = 900) -> None:
        """Save the current plot (spectra, labels, stack mode) as a PNG or SVG
        at a fixed size -- 16:9 by default -- whatever shape the on-screen
        plot has been stretched to. Renders an offscreen copy so the visible
        plot isn't resized."""
        import pyqtgraph.exporters as exporters

        copy = SpectrumPlotWidget()
        try:
            copy._stack_mode = self._stack_mode
            copy._auto_label_enabled = self._auto_label_enabled
            copy._auto_label_params = dict(self._auto_label_params)
            # Lay the copy out at the export size without it ever appearing
            # on screen; an unshown widget keeps its default 4:3 geometry.
            copy.setAttribute(Qt.WA_DontShowOnScreen, True)
            copy.resize(width, height)
            copy.show()
            QApplication.processEvents()
            copy.refresh(self._entries, self._highlight_row)
            QApplication.processEvents()
            copy.plot_item.vb.autoRange()
            if str(path).lower().endswith(".svg"):
                exporter = exporters.SVGExporter(copy.plot_item)
            else:
                exporter = exporters.ImageExporter(copy.plot_item)
                exporter.parameters()["width"] = width
                exporter.parameters()["height"] = height
            exporter.export(str(path))
        finally:
            copy.hide()
            copy.deleteLater()

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
        self._relabel_timer.stop()
        for item in self._auto_label_items:
            self.plot_item.removeItem(item)
        self._auto_label_items.clear()
        self._label_items.clear()
        self._label_candidates = []
        self._label_curves = []

    def _schedule_relabel(self, *_args) -> None:
        if self._label_candidates:
            self._relabel_timer.start()

    def _place_auto_labels(self) -> None:
        """Draw a marker + integer wavenumber label for each candidate,
        placed clear of the traces, the other labels and the plot edge (see
        ``openftir.label_layout``), with a leader line when a label had to
        move away from its peak.

        Manual picks go first and are always shown; auto candidates follow,
        deepest dip / tallest peak first, so when space runs out it's the
        weakest band that loses its label. Items are added with
        ``ignoreBounds=True`` so they never affect autorange.
        """
        for item in self._label_items:
            self.plot_item.removeItem(item)
            if item in self._auto_label_items:
                self._auto_label_items.remove(item)
        self._label_items.clear()
        candidates = self._label_candidates
        if not candidates:
            return

        vb = self.plot_item.vb
        (x_lo, x_hi), (y_lo, y_hi) = vb.viewRange()
        p0 = vb.mapFromView(QPointF(x_lo, y_lo))
        p1 = vb.mapFromView(QPointF(x_hi, y_hi))
        sx = (p1.x() - p0.x()) / ((x_hi - x_lo) or 1.0)
        sy = (p1.y() - p0.y()) / ((y_hi - y_lo) or 1.0)
        if not sx or not sy or vb.width() < 10 or vb.height() < 10:
            return

        def to_px(x, y):
            return p0.x() + (np.asarray(x) - x_lo) * sx, p0.y() + (np.asarray(y) - y_lo) * sy

        def to_data(px, py):
            return x_lo + (px - p0.x()) / sx, y_lo + (py - p0.y()) / sy

        font = QFont()
        font.setPointSize(_AUTO_LABEL_FONT_PT)
        bold_font = QFont(font)
        bold_font.setBold(True)

        def _significance(c: tuple[float, float, bool, QColor, bool]) -> float:
            _position, value, down, _color, _manual = c
            return (100.0 - value) if down else value

        ordered = sorted(candidates, key=lambda c: (0 if c[4] else 1, -_significance(c), c[0]))
        texts = []
        requests = []
        for position, value, down, color, manual in ordered:
            text = pg.TextItem(text=str(int(round(position))), color=color, anchor=(0.5, 0.0) if down else (0.5, 1.0))
            text.setFont(bold_font if manual else font)
            margin = text.textItem.document().documentMargin()
            rect = text.textItem.boundingRect()
            px, py = to_px(position, value)
            requests.append(LabelRequest(
                float(px), float(py), rect.width() - 2 * margin, rect.height() - 2 * margin,
                below=down, must_show=manual,
            ))
            texts.append((text, margin))

        curves = [to_px(x, y) for x, y in self._label_curves]
        bounds = (2.0, 2.0, vb.width() - 2.0, vb.height() - 2.0)
        placements = place_labels(requests, curves, bounds, marker_radius=_MANUAL_LABEL_MARKER_SIZE / 2)

        def add(item):
            self.plot_item.addItem(item, ignoreBounds=True)
            self._auto_label_items.append(item)
            self._label_items.append(item)

        for (position, value, down, color, manual), (text, margin), placement in zip(ordered, texts, placements):
            if placement is None:
                continue  # an auto band with nowhere to put its label
            marker_symbol = ("t" if down else "t1") if manual else "o"
            marker_size = _MANUAL_LABEL_MARKER_SIZE if manual else _AUTO_LABEL_MARKER_SIZE
            add(pg.ScatterPlotItem(
                x=[position], y=[value], symbol=marker_symbol, size=marker_size,
                brush=pg.mkBrush(color), pen=pg.mkPen(None),
            ))
            ax_, ay_ = placement.anchor_point(down)
            if placement.leader:
                lx, ly = to_data(ax_, ay_)
                leader = pg.PlotCurveItem(x=[position, lx], y=[value, ly], pen=_cosmetic_pen(_LEADER_COLOR, width=0.8))
                add(leader)
            # The text item's box includes a document margin around the glyphs.
            tx, ty = to_data(ax_, ay_ - margin if down else ay_ + margin)
            text.setPos(tx, ty)
            add(text)

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
        self, x: np.ndarray, y: np.ndarray, label: str, y_unit: YUnit = YUnit.SINGLE_BEAM, solid: bool = False
    ) -> None:
        """Show a preview trace (live single beam while collecting, or the
        finished background). ``solid`` draws it as a muted solid line
        instead of the thin dotted in-progress style."""
        self.clear_live_preview()
        if solid:
            pen = _cosmetic_pen("#6b7480", width=_SPECTRUM_PEN_WIDTH)
        else:
            pen = _cosmetic_pen("#7f7f7f", width=_OVERLAY_PEN_WIDTH, style=Qt.DotLine)
        self._preview_item = self.plot_item.plot(x, y, pen=pen, name=label)
        self._preview_y_unit = y_unit
        self._update_axis_labels()
        self._update_legend_visibility()
        self.plot_item.vb.autoRange()

    def has_live_preview(self) -> bool:
        return self._preview_item is not None

    def _update_legend_visibility(self) -> None:
        # An empty legend draws as a stray little box in the corner.
        legend = self.plot_item.legend
        if legend is not None:
            legend.setVisible(bool(legend.items))

    def clear_live_preview(self) -> None:
        if self._preview_item is not None:
            self.plot_item.removeItem(self._preview_item)
            if self.plot_item.legend is not None:
                self.plot_item.legend.removeItem(self._preview_item)
            self._preview_item = None
        self._preview_y_unit = None
        self._update_axis_labels()
        self._update_legend_visibility()
