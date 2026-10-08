"""The main spectrum plot: a pyqtgraph view with IR conventions baked in.

Wavenumber axes are plotted high-to-low (left-to-right), the y axis label
follows the active ``YUnit``, and the plot background/foreground follow the
application palette so it looks native in both light and dark desktop themes.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from openftir.processing import Peak
from openftir.spectrum import XUnit, YUnit
from openftir.gui.spectrum_list import SpectrumEntry

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

# Vertical gap between stacked traces, as a fraction of the tallest trace's span.
_STACK_GAP_FRACTION = 1.15


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
        self._highlight_row: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_item: pg.PlotItem = self.plot_widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.25)
        self.plot_item.invertX(True)  # IR convention: high wavenumber on the left
        self.plot_item.addLegend(offset=(10, 10))
        self.plot_item.setLabel("bottom", "Wavenumber", units="cm⁻¹")
        self.plot_item.setLabel("left", "Absorbance")
        layout.addWidget(self.plot_widget)

        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._hline = pg.InfiniteLine(angle=0, movable=False)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self.plot_item.addItem(self._vline, ignoreBounds=True)
        self.plot_item.addItem(self._hline, ignoreBounds=True)

        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self.apply_palette()

    # -- appearance -----------------------------------------------------------

    def apply_palette(self) -> None:
        """Match the plot colours to the current application (light/dark) palette."""
        palette = QApplication.palette()
        bg = palette.color(palette.ColorRole.Base)
        fg = palette.color(palette.ColorRole.Text)
        self.plot_widget.setBackground(bg)
        for axis_name in ("bottom", "left"):
            axis = self.plot_item.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
            # Spectroscopists expect raw cm-1/absorbance values, never an
            # auto-inserted k/M SI prefix on the axis label.
            axis.enableAutoSIPrefix(False)
        self._vline.setPen(pg.mkPen(fg, style=Qt.DashLine))
        self._hline.setPen(pg.mkPen(fg, style=Qt.DashLine))

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

    # -- drawing ------------------------------------------------------------------

    def _axis_labels(self, entries: list[SpectrumEntry]) -> None:
        if not entries:
            return
        s = entries[0].spectrum
        x_name, x_unit = _X_LABELS.get(s.x_unit, (s.x_unit.value, ""))
        y_name, y_unit = _Y_LABELS.get(s.y_unit, (s.y_unit.value, ""))
        self.plot_item.setLabel("bottom", x_name, units=x_unit)
        self.plot_item.setLabel("left", y_name, units=y_unit)

    def refresh(self, entries: Iterable[SpectrumEntry], highlight_row: int | None = None) -> None:
        """Redraw every visible entry. ``highlight_row`` thickens that trace."""
        entries = list(entries)
        self._highlight_row = highlight_row
        for curve in self._curves:
            self.plot_item.removeItem(curve)
        self._curves.clear()
        self.plot_item.legend.clear()

        visible = [(i, e) for i, e in enumerate(entries) if e.visible]
        self._axis_labels([e for _, e in visible] or entries)

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
            width = 2.5 if row == highlight_row else 1.3
            pen = pg.mkPen(entry.color, width=width)
            y = s.y + offsets[k] if self._stack_mode else s.y
            curve = self.plot_item.plot(s.x, y, pen=pen, name=entry.name)
            self._curves.append(curve)

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
        pen = pg.mkPen("#2ca02c", width=1.5, style=Qt.DashLine)
        self._baseline_item = self.plot_item.plot(x, y, pen=pen, name="baseline preview")

    def clear_baseline_preview(self) -> None:
        if self._baseline_item is not None:
            self.plot_item.removeItem(self._baseline_item)
            self._baseline_item = None

    # -- live collect preview --------------------------------------------------------

    def show_live_preview(self, x: np.ndarray, y: np.ndarray, label: str) -> None:
        self.clear_live_preview()
        pen = pg.mkPen("#7f7f7f", width=1.5, style=Qt.DotLine)
        self._preview_item = self.plot_item.plot(x, y, pen=pen, name=label)

    def clear_live_preview(self) -> None:
        if self._preview_item is not None:
            self.plot_item.removeItem(self._preview_item)
            self._preview_item = None
