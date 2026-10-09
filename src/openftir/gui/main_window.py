"""The OpenFTIR main window: wires the plot, spectrum list, processing
menu/toolbar, collect panel, library panel and details panel together.

This module only orchestrates the GUI; all spectral math lives in
``openftir.processing`` and all file formats in ``openftir.io``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDockWidget,
    QInputDialog,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from openftir import __version__, processing
from openftir.spectrum import Spectrum, XUnit, YUnit
from openftir.gui import file_dialogs
from openftir.gui.collect_panel import CollectPanel
from openftir.gui.details_panel import DetailsPanel
from openftir.gui.dialogs import (
    AtrDialog,
    BaselineDialog,
    CropDialog,
    DerivativeDialog,
    NormalizeDialog,
    PeaksDialog,
    ProbeDialog,
    SmoothDialog,
    SubtractDialog,
)
from openftir.gui.library_panel import LibraryPanel
from openftir.gui.peaks_panel import PeaksPanel
from openftir.gui.plot import SpectrumPlotWidget
from openftir.gui.spectrum_list import ColorRole, SpectrumListModel

_MAX_RECENT_FILES = 10
_BASELINE_METHODS = {
    "als": processing.baseline_als,
    "polynomial": processing.baseline_polynomial,
    "rubberband": processing.baseline_rubberband,
}


def _io_module():
    """Import ``openftir.io`` lazily; it is being built by another agent."""
    import openftir.io as io_mod

    return io_mod


class _PlotAspectContainer(QWidget):
    """Keeps the spectrum plot at a 16:9 aspect ratio, centred in the
    central area, instead of stretching to fill whatever odd shape the
    docks leave around it -- a very wide-short or tall-narrow plot is
    harder to read. Sized/positioned manually in ``resizeEvent`` so it
    still behaves (shrinks proportionally, no overlap) at small window
    sizes."""

    _ASPECT = 16 / 9

    def __init__(self, plot_widget: QWidget, parent=None):
        super().__init__(parent)
        plot_widget.setParent(self)
        self._plot_widget = plot_widget
        self._layout_plot()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_plot()

    def _layout_plot(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        plot_w, plot_h = float(w), w / self._ASPECT
        if plot_h > h:
            plot_w, plot_h = h * self._ASPECT, float(h)
        x = int((w - plot_w) / 2)
        y = int((h - plot_h) / 2)
        self._plot_widget.setGeometry(x, y, int(plot_w), int(plot_h))


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenFTIR")
        self.setAcceptDrops(True)
        self.settings = QSettings("OpenFTIR", "OpenFTIR")

        self.model = SpectrumListModel(self)
        self.model.dataChanged.connect(self._refresh_plot)
        self.model.rowsInserted.connect(self._refresh_plot)
        self.model.rowsRemoved.connect(self._refresh_plot)
        self.model.visibility_changed.connect(self._refresh_plot)

        self.plot = SpectrumPlotWidget(self)
        self.plot.cursor_moved.connect(self._on_cursor_moved)
        self._central = _PlotAspectContainer(self.plot, self)
        self.setCentralWidget(self._central)

        self._build_list_dock()
        self._build_details_dock()
        self._build_peaks_dock()
        self._build_collect_dock()
        self._build_library_dock()
        self._build_toolbar()
        self._build_menus()

        self.statusBar().showMessage("Ready")
        self._update_recent_files_menu()
        self._apply_window_geometry()

    # -- docks ------------------------------------------------------------------------

    def _build_list_dock(self) -> None:
        dock = QDockWidget("Spectra", self)
        dock.setObjectName("spectraDock")
        container = QWidget()
        layout = QVBoxLayout(container)

        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_view)

        btn_row = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        btn_layout = QHBoxLayout(btn_row)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_current)
        color_btn = QPushButton("Color...")
        color_btn.clicked.connect(self._recolor_current)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(rename_btn)
        btn_layout.addWidget(color_btn)
        btn_layout.addWidget(delete_btn)
        layout.addWidget(btn_row)

        dock.setWidget(container)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_details_dock(self) -> None:
        dock = QDockWidget("Details", self)
        dock.setObjectName("detailsDock")
        self.details_panel = DetailsPanel()
        dock.setWidget(self.details_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_peaks_dock(self) -> None:
        dock = QDockWidget("Peaks", self)
        dock.setObjectName("peaksDock")
        self.peaks_panel = PeaksPanel()
        dock.setWidget(self.peaks_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.tabifyDockWidget(self.findChild(QDockWidget, "detailsDock"), dock)

    def _build_collect_dock(self) -> None:
        dock = QDockWidget("Collect", self)
        dock.setObjectName("collectDock")
        self.collect_panel = CollectPanel()
        self.collect_panel.spectrum_collected.connect(self._on_spectrum_collected)
        self.collect_panel.preview_ready.connect(self._on_preview_ready)
        self.collect_panel.status_message.connect(self.statusBar().showMessage)
        dock.setWidget(self.collect_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_library_dock(self) -> None:
        dock = QDockWidget("Library", self)
        dock.setObjectName("libraryDock")
        self.library_panel = LibraryPanel(get_selected=self._get_selected_name_spectrum)
        self.library_panel.overlay_requested.connect(self._on_library_overlay)
        dock.setWidget(self.library_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.tabifyDockWidget(dock, self.findChild(QDockWidget, "collectDock"))

    # -- toolbar / menus ----------------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)

        self.stack_action = QAction("Stack/Offset", self)
        self.stack_action.setCheckable(True)
        self.stack_action.toggled.connect(self._on_stack_toggled)
        toolbar.addAction(self.stack_action)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open)
        file_menu.addAction(open_action)

        self.recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self.recent_menu)

        save_action = QAction("&Save Selected As...", self)
        save_action.triggered.connect(self._on_save_selected)
        file_menu.addAction(save_action)

        export_action = QAction("&Export Plot As...", self)
        export_action.triggered.connect(self._on_export_plot)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        process_menu = self.menuBar().addMenu("&Process")

        to_abs = QAction("Convert to Absorbance", self)
        to_abs.triggered.connect(lambda: self._apply(processing.to_absorbance, "convert to absorbance"))
        process_menu.addAction(to_abs)

        to_trans = QAction("Convert to %Transmittance", self)
        to_trans.triggered.connect(lambda: self._apply(processing.to_transmittance, "convert to %T"))
        process_menu.addAction(to_trans)

        process_menu.addSeparator()

        baseline_action = QAction("Baseline Correction...", self)
        baseline_action.triggered.connect(self._on_baseline)
        process_menu.addAction(baseline_action)

        smooth_action = QAction("Smoothing (Savitzky-Golay)...", self)
        smooth_action.triggered.connect(self._on_smooth)
        process_menu.addAction(smooth_action)

        derivative_action = QAction("Derivative...", self)
        derivative_action.triggered.connect(self._on_derivative)
        process_menu.addAction(derivative_action)

        normalize_action = QAction("Normalize...", self)
        normalize_action.triggered.connect(self._on_normalize)
        process_menu.addAction(normalize_action)

        atr_action = QAction("ATR Correction...", self)
        atr_action.triggered.connect(self._on_atr)
        process_menu.addAction(atr_action)

        process_menu.addSeparator()

        crop_action = QAction("Crop...", self)
        crop_action.triggered.connect(self._on_crop)
        process_menu.addAction(crop_action)

        subtract_action = QAction("Subtract...", self)
        subtract_action.triggered.connect(self._on_subtract)
        process_menu.addAction(subtract_action)

        average_action = QAction("Average Selected", self)
        average_action.triggered.connect(self._on_average)
        process_menu.addAction(average_action)

        process_menu.addSeparator()

        peaks_action = QAction("Peak Picking...", self)
        peaks_action.triggered.connect(self._on_peak_picking)
        process_menu.addAction(peaks_action)

        help_menu = self.menuBar().addMenu("&Help")

        probe_action = QAction("Probe Instrument Connection...", self)
        probe_action.triggered.connect(self._on_probe_instrument)
        help_menu.addAction(probe_action)

        help_menu.addSeparator()

        about_action = QAction("About OpenFTIR", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # -- selection helpers --------------------------------------------------------------

    def _current_row(self) -> int | None:
        index = self.list_view.currentIndex()
        return index.row() if index.isValid() else None

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.list_view.selectionModel().selectedIndexes()})

    def _get_selected_name_spectrum(self):
        row = self._current_row()
        if row is None:
            return None
        entry = self.model.entry_at(row)
        return entry.name, entry.spectrum

    def _on_selection_changed(self, current, _previous) -> None:
        self._refresh_plot()
        if current.isValid():
            entry = self.model.entry_at(current.row())
            self.details_panel.show_spectrum(entry.name, entry.spectrum)
        else:
            self.details_panel.clear()

    def _refresh_plot(self, *_args) -> None:
        self.plot.refresh(self.model.entries(), highlight_row=self._current_row())

    # -- list dock actions --------------------------------------------------------------

    def _rename_current(self) -> None:
        row = self._current_row()
        if row is None:
            return
        entry = self.model.entry_at(row)
        name, ok = QInputDialog.getText(self, "Rename Spectrum", "New name:", text=entry.name)
        if ok and name.strip():
            self.model.setData(self.model.index(row), name.strip(), Qt.EditRole)

    def _recolor_current(self) -> None:
        row = self._current_row()
        if row is None:
            return
        entry = self.model.entry_at(row)
        color = QColorDialog.getColor(entry.color, self, "Spectrum Color")
        if color.isValid():
            self.model.setData(self.model.index(row), color, ColorRole)

    def _delete_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            self.model.remove(row)

    def _on_stack_toggled(self, checked: bool) -> None:
        self.plot.set_stack_mode(checked)
        self._refresh_plot()

    def _on_cursor_moved(self, x: float, y: float) -> None:
        self.statusBar().showMessage(f"x = {x:.2f}   y = {y:.4f}")

    # -- file menu ------------------------------------------------------------------------

    def _on_open(self) -> None:
        try:
            io_mod = _io_module()
            filters = io_mod.SUPPORTED_READ
        except (ImportError, AttributeError):
            filters = {}
        filter_str = file_dialogs.build_filter_string(filters)
        paths = file_dialogs.get_open_paths(self, "Open Spectra", filter_str)
        if paths:
            self._open_paths(paths)

    def _open_paths(self, paths: list[str]) -> None:
        try:
            io_mod = _io_module()
        except ImportError as exc:
            QMessageBox.critical(self, "Open", f"File I/O is not available yet:\n{exc}")
            return

        errors = []
        loaded_any = False
        for path in paths:
            try:
                spectra = io_mod.load(path)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user, never crashes
                errors.append(f"{path}: {exc}")
                continue
            stem = Path(path).stem
            for i, spectrum in enumerate(spectra):
                name = spectrum.title or (stem if len(spectra) == 1 else f"{stem} [{i + 1}]")
                self.model.add(spectrum, name=name)
                loaded_any = True
            if spectra:
                self._add_recent_file(path)

        if loaded_any:
            self.plot.full_scale()
        if errors:
            QMessageBox.critical(self, "Open", "Some files could not be opened:\n\n" + "\n".join(errors))

    def _on_save_selected(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Save", "Select a spectrum first.")
            return
        try:
            io_mod = _io_module()
            filters = io_mod.SUPPORTED_WRITE
        except (ImportError, AttributeError) as exc:
            QMessageBox.critical(self, "Save", f"File I/O is not available yet:\n{exc}")
            return

        entry = self.model.entry_at(row)
        filter_str = file_dialogs.build_filter_string(filters, include_all_supported=False)
        default_ext = next(iter(filters), "")
        default_name = file_dialogs.sanitize_filename(entry.name) + default_ext
        path = file_dialogs.get_save_path(self, "Save Spectrum As", default_name, filter_str)
        if not path:
            return
        try:
            io_mod.save([entry.spectrum], path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save", f"Could not save file:\n{exc}")

    def _on_export_plot(self) -> None:
        path = file_dialogs.get_save_path(
            self, "Export Plot", "OpenFTIR plot.png", "PNG Image (*.png);;SVG Image (*.svg)"
        )
        if not path:
            return
        try:
            import pyqtgraph.exporters as exporters

            if path.lower().endswith(".svg"):
                exporter = exporters.SVGExporter(self.plot.plot_item)
            else:
                exporter = exporters.ImageExporter(self.plot.plot_item)
            exporter.export(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export Plot", f"Could not export plot:\n{exc}")

    def _add_recent_file(self, path: str) -> None:
        recent = self.settings.value("recentFiles", [], type=list)
        recent = [p for p in recent if p != path]
        recent.insert(0, path)
        recent = recent[:_MAX_RECENT_FILES]
        self.settings.setValue("recentFiles", recent)
        self._update_recent_files_menu()

    def _update_recent_files_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.value("recentFiles", [], type=list)
        if not recent:
            empty = QAction("(no recent files)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in recent:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, p=path: self._open_paths([p]))
            self.recent_menu.addAction(action)

    # -- drag and drop --------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self._open_paths(paths)
            event.acceptProposedAction()

    # -- processing menu --------------------------------------------------------------------

    def _apply(self, fn, label: str, *args, new_name: str | None = None, **kwargs) -> int | None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Process", "Select a spectrum first.")
            return None
        entry = self.model.entry_at(row)
        try:
            result = fn(entry.spectrum, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Process", f"{label} failed:\n{exc}")
            return None
        name = new_name or f"{entry.name} [{label}]"
        new_row = self.model.add(result, name=name)
        self.list_view.setCurrentIndex(self.model.index(new_row))
        return new_row

    def _on_baseline(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Baseline", "Select a spectrum first.")
            return
        spectrum = self.model.entry_at(row).spectrum

        def preview(method: str, params: dict) -> None:
            try:
                _corrected, baseline_spec = _BASELINE_METHODS[method](
                    spectrum, return_baseline=True, **params
                )
            except Exception:
                self.plot.clear_baseline_preview()
                return
            self.plot.show_baseline_preview(baseline_spec.x, baseline_spec.y)

        dialog = BaselineDialog(on_change=preview, parent=self)
        accepted = dialog.exec()
        self.plot.clear_baseline_preview()
        if not accepted:
            return
        method, params = dialog.method_params()
        self._apply(lambda s: _BASELINE_METHODS[method](s, **params), f"{method} baseline")

    def _on_smooth(self) -> None:
        dialog = SmoothDialog(self)
        if dialog.exec():
            params = dialog.params()
            self._apply(lambda s: processing.savgol(s, **params), "smoothed")

    def _on_derivative(self) -> None:
        dialog = DerivativeDialog(self)
        if dialog.exec():
            params = dialog.params()
            self._apply(lambda s: processing.derivative(s, **params), f"{params['order']}-derivative")

    def _on_normalize(self) -> None:
        dialog = NormalizeDialog(self)
        if dialog.exec():
            params = dialog.params()
            self._apply(lambda s: processing.normalize(s, **params), "normalized")

    def _on_atr(self) -> None:
        dialog = AtrDialog(self)
        if dialog.exec():
            params = dialog.params()
            self._apply(lambda s: processing.atr_correction(s, **params), "ATR corrected")

    def _on_crop(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Crop", "Select a spectrum first.")
            return
        spectrum = self.model.entry_at(row).spectrum
        x_min = float(np.min(spectrum.x)) if len(spectrum.x) else 0.0
        x_max = float(np.max(spectrum.x)) if len(spectrum.x) else 1.0
        dialog = CropDialog(x_min, x_max, self)
        if dialog.exec():
            params = dialog.params()
            self._apply(lambda s: processing.crop(s, params["x_min"], params["x_max"]), "cropped")

    def _on_subtract(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Subtract", "Select a spectrum first.")
            return
        a_entry = self.model.entry_at(row)
        choices = [
            (i, e.name) for i, e in enumerate(self.model.entries()) if i != row
        ]
        if not choices:
            QMessageBox.information(self, "Subtract", "Need at least one other spectrum.")
            return

        def auto_factor(b_row: int) -> float:
            b_spectrum = self.model.spectrum_at(b_row)
            return processing.auto_subtract_factor(a_entry.spectrum, b_spectrum)

        dialog = SubtractDialog(choices, auto_factor, self)
        if dialog.exec():
            params = dialog.params()
            b_spectrum = self.model.spectrum_at(params["b_row"])
            self._apply(
                lambda s: processing.subtract(s, b_spectrum, factor=params["factor"]),
                "subtracted",
            )

    def _on_average(self) -> None:
        rows = self._selected_rows()
        if len(rows) < 2:
            QMessageBox.information(self, "Average", "Select two or more spectra to average.")
            return
        spectra = [self.model.spectrum_at(r) for r in rows]
        try:
            result = processing.average(spectra)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Average", f"Average failed:\n{exc}")
            return
        new_row = self.model.add(result, name=f"Average of {len(rows)} spectra")
        self.list_view.setCurrentIndex(self.model.index(new_row))

    def _on_peak_picking(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "Peak Picking", "Select a spectrum first.")
            return
        spectrum = self.model.entry_at(row).spectrum
        y_span = float(np.ptp(spectrum.y)) if len(spectrum.y) else 1.0

        def preview(params: dict) -> None:
            try:
                peaks = processing.find_peaks(spectrum, **params)
            except Exception:
                peaks = []
            self.plot.show_peaks(peaks)
            self.peaks_panel.set_peaks(peaks)

        dialog = PeaksDialog(y_span, on_change=preview, parent=self)
        if not dialog.exec():
            self.plot.clear_peaks()
            self.peaks_panel.clear()

    # -- help menu --------------------------------------------------------------------

    def _on_probe_instrument(self) -> None:
        dialog = ProbeDialog(self)
        dialog.exec()

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About OpenFTIR",
            f"<b>OpenFTIR</b> version {__version__}<br><br>"
            "Open-source FTIR control and analysis.<br>"
            "Licensed under the MIT License.<br><br>"
            "Not affiliated with Thermo Fisher Scientific.",
        )

    # -- collect panel callbacks ----------------------------------------------------------

    def _on_spectrum_collected(self, spectrum: Spectrum, name: str) -> None:
        self.plot.clear_live_preview()
        row = self.model.add(spectrum, name=name)
        self.list_view.setCurrentIndex(self.model.index(row))

    def _on_preview_ready(self, spectrum: Spectrum, label: str) -> None:
        self.plot.show_live_preview(spectrum.x, spectrum.y, label, spectrum.y_unit)

    # -- library panel callbacks ------------------------------------------------------------

    def _on_library_overlay(self, spectrum: Spectrum, name: str) -> None:
        row = self.model.add(spectrum, name=f"{name} (library)")
        self.list_view.setCurrentIndex(self.model.index(row))

    # -- window geometry ----------------------------------------------------------------

    def _apply_window_geometry(self) -> None:
        """Fit the window to the screen's available area (90% of its width
        and height, centred) rather than a fixed size that can run off the
        bottom of the screen (e.g. under the macOS Dock). A previous
        session's saved geometry is used instead if it still fits on the
        current screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)

        saved = self.settings.value("windowGeometry")
        if saved and self.restoreGeometry(saved) and available.contains(self.frameGeometry()):
            return

        width = max(1, int(available.width() * 0.9))
        height = max(1, int(available.height() * 0.9))
        x = available.x() + (available.width() - width) // 2
        y = available.y() + (available.height() - height) // 2
        self.setGeometry(x, y, width, height)

    def closeEvent(self, event) -> None:
        self.settings.setValue("windowGeometry", self.saveGeometry())
        super().closeEvent(event)
