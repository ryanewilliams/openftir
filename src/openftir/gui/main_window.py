"""The OpenFTIR main window: wires the plot, spectrum list, processing
menu/toolbar, collect panel, library panel and details panel together.

This module only orchestrates the GUI; all spectral math lives in
``openftir.processing`` and all file formats in ``openftir.io``.

Two UI modes share this one window: **Simple** (the default -- a minimal
top bar + plot + spectrum chips, for routine background/sample collection)
and **Advanced** (the full dock/toolbar/menu layout this app always had).
Both modes are views over the same state -- one :class:`SpectrumPlotWidget`
instance, one :class:`SpectrumListModel`, and one
:class:`~openftir.gui.collect_controller.CollectController` -- so switching
modes never duplicates or loses anything; see ``_apply_mode``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from openftir import __version__, processing
from openftir.spectrum import Spectrum, XUnit, YUnit
from openftir.gui import file_dialogs
from openftir.gui.collect_controller import CollectController
from openftir.gui.collect_panel import CollectPanel
from openftir.gui.details_panel import DetailsPanel
from openftir.gui.dialogs import (
    AtrDialog,
    BaselineDialog,
    CropDialog,
    DerivativeDialog,
    NormalizeDialog,
    PeakLabelSettingsDialog,
    PeaksDialog,
    ProbeDialog,
    SmoothDialog,
    SubtractDialog,
)
from openftir.gui.library_panel import LibraryPanel
from openftir.gui.peaks_panel import PeaksPanel
from openftir.gui.plot import SpectrumPlotWidget
from openftir.gui.simple_panels import SimpleBottomBar, SimpleSettingsStrip, SimpleTopBar
from openftir.gui.spectrum_list import ColorRole, SpectrumListModel
from openftir.processing import Peak

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
    """Holds the spectrum plot at a fixed 16:9 aspect ratio: its height
    follows its width, so the plot never turns into a tall or squashed
    shape whatever space is left for it. Width shrinks if the height
    available would otherwise be exceeded.

    There is exactly one instance of this (and of the plot widget it wraps)
    for the whole app; Simple and Advanced mode's central widgets both hold
    a reference to it and move it into their own layout when they become
    active -- see ``_CentralArea.activate``/``_SimpleCentralArea.activate``.
    """

    _ASPECT = 16 / 9

    def __init__(self, plot_widget: QWidget, parent=None):
        super().__init__(parent)
        plot_widget.setParent(self)
        self._plot_widget = plot_widget
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumSize(320, 180)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return int(round(width / self._ASPECT))

    def sizeHint(self):
        return QSize(960, 540)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        plot_w, plot_h = float(w), w / self._ASPECT
        if plot_h > h:
            plot_w, plot_h = h * self._ASPECT, float(h)
        self._plot_widget.setGeometry(int((w - plot_w) / 2), 0, int(plot_w), int(plot_h))


class _CentralArea(QWidget):
    """Advanced mode's central widget: plot at the top (16:9), Peaks/Details
    tabs filling the space below."""

    def __init__(self, plot_container: _PlotAspectContainer, peaks_panel: QWidget, details_panel: QWidget, parent=None):
        super().__init__(parent)
        self.plot_container = plot_container
        self.tabs = QTabWidget(self)
        self.tabs.addTab(peaks_panel, "Peaks")
        self.tabs.addTab(details_panel, "Details")
        self.tabs.setMinimumHeight(200)  # keep several peak rows visible
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot_container, 0)
        layout.addWidget(self.tabs, 1)

    def activate(self) -> None:
        """Reclaim the shared plot container into this layout (it may
        currently be living inside Simple mode's central widget)."""
        # Simple mode pins the container to an exact width; undo that here.
        self.plot_container.setMinimumWidth(0)
        self.plot_container.setMaximumWidth(16777215)
        self.layout().insertWidget(0, self.plot_container)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Height-for-width isn't honoured by QVBoxLayout on its own; give the
        # plot exactly 9/16 of the width, but never squeeze the tabs below
        # their minimum.
        max_plot_h = max(self.height() - self.tabs.minimumHeight(), 180)
        self.plot_container.setFixedHeight(min(self.plot_container.heightForWidth(self.width()), max_plot_h))


class _SimplePlotToolbar(QWidget):
    """The slim row of flat buttons above the plot in Simple mode: Pick
    peaks / Auto labels (both mirror a shared QAction so Advanced mode's
    own menu/toolbar entries -- see MainWindow._build_menus -- always agree
    on the checked state) and Clear picks."""

    def __init__(self, pick_peaks_action: QAction, auto_label_action: QAction, clear_action: QAction, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 2, 14, 2)
        row.setSpacing(4)

        for action in (pick_peaks_action, auto_label_action):
            btn = QToolButton(self)
            btn.setDefaultAction(action)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            row.addWidget(btn)

        # Its own button text ("Clear picks", matching the spec's Simple-mode
        # wording) rather than setDefaultAction's auto-synced "Clear manual
        # peaks" (used in Advanced mode's Process menu/toolbar) -- same
        # action, triggered the same way, just a shorter label here.
        clear_btn = QToolButton(self)
        clear_btn.setText("Clear picks")
        clear_btn.setToolTip(clear_action.toolTip())
        clear_btn.setAutoRaise(True)
        clear_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        clear_btn.clicked.connect(clear_action.trigger)
        row.addWidget(clear_btn)

        # Shown only while pick mode is on, so it's obvious what a click does.
        self.pick_hint = QLabel("Click a band to label it · click a picked label to remove it · Esc to finish")
        self.pick_hint.setStyleSheet("color: #174a86; padding-left: 8px;")
        self.pick_hint.setVisible(pick_peaks_action.isChecked())
        pick_peaks_action.toggled.connect(self.pick_hint.setVisible)
        row.addWidget(self.pick_hint)

        row.addStretch(1)

        # Make toggle state visible: flat buttons that read as "pressed" in
        # the accent colour when on (pick mode / auto labels).
        self.setStyleSheet(
            "QToolButton { padding: 3px 10px; border: 1px solid transparent; border-radius: 4px; }"
            "QToolButton:hover { border-color: #c9d3df; background: #f1f4f8; }"
            "QToolButton:checked { background: #dce9f8; border-color: #9cbce3; color: #174a86; }"
        )

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(900, 30)


class _PeaksSidePanel(QWidget):
    """Simple mode's ~260 px right-hand "Peaks" panel: a title + the shared
    PeaksPanel table/copy-button widget (a second instance from Advanced
    mode's own Peaks tab -- both are kept in sync, see
    MainWindow._refresh_peaks_panel)."""

    WIDTH = 260

    def __init__(self, peaks_panel: QWidget, parent=None):
        super().__init__(parent)
        # Width is set by _SimpleCentralArea (min/max), so the panel takes
        # whatever the 16:9 plot leaves instead of a fixed 260 px.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 14, 0)
        layout.setSpacing(4)

        title = QLabel("Peaks")
        title.setStyleSheet("font-weight: 600; color: #444444;")
        layout.addWidget(title)
        layout.addWidget(peaks_panel, 1)


class _SimpleCentralArea(QWidget):
    """Simple mode's central widget: top bar, settings strip, the slim
    plot-toolbar row, plot (16:9) + Peaks side panel, bottom bar."""

    def __init__(
        self,
        plot_container: _PlotAspectContainer,
        top_bar: QWidget,
        settings_strip: QWidget,
        plot_toolbar: QWidget,
        peaks_side_panel: QWidget,
        bottom_bar: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        self.plot_container = plot_container
        self.top_bar = top_bar
        self.settings_strip = settings_strip
        self.plot_toolbar = plot_toolbar
        self.peaks_side_panel = peaks_side_panel
        self.bottom_bar = bottom_bar

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(top_bar, 0)
        layout.addWidget(settings_strip, 0)
        layout.addWidget(plot_toolbar, 0)

        self._body = QWidget(self)
        self._body_layout = QHBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.addWidget(plot_container, 0)
        self._body_layout.addSpacing(12)
        self._body_layout.addWidget(peaks_side_panel, 1)
        peaks_side_panel.setMinimumWidth(240)
        peaks_side_panel.setMaximumWidth(420)
        self._body_layout.addStretch(0)
        layout.addWidget(self._body, 1)

        layout.addWidget(bottom_bar, 0)

    def activate(self) -> None:
        """Reclaim the shared plot container into this layout (it may
        currently be living inside Advanced mode's central widget)."""
        self._body_layout.insertWidget(0, self.plot_container)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        reserved = (
            self.top_bar.sizeHint().height()
            + self.settings_strip.sizeHint().height()
            + self.plot_toolbar.sizeHint().height()
            + self.bottom_bar.sizeHint().height()
        )
        # Size the plot box to exactly 16:9: as tall as the space allows, but
        # leaving the Peaks panel at least its minimum width. The panel then
        # takes the remaining width instead of empty margins around the plot.
        max_plot_h = max(self.height() - reserved, 160)
        available_w = max(self.width() - self.peaks_side_panel.minimumWidth() - 12, 160)
        plot_w = min(available_w, int(max_plot_h * 16 / 9))
        self.plot_container.setFixedSize(plot_w, self.plot_container.heightForWidth(plot_w))


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenFTIR")
        self.setAcceptDrops(True)
        self.settings = QSettings("OpenFTIR", "OpenFTIR")

        # Tracks whether the manual Peak Picking dialog's results are the
        # ones currently shown in the Peaks table, so the auto-label
        # refresh (see _refresh_peaks_panel) leaves them alone until the
        # user moves on (new selection, or cancelling the dialog).
        self._manual_peak_pick_active = False

        self.model = SpectrumListModel(self)
        self.model.dataChanged.connect(self._refresh_plot)
        self.model.rowsInserted.connect(self._refresh_plot)
        self.model.rowsRemoved.connect(self._refresh_plot)
        self.model.visibility_changed.connect(self._refresh_plot)
        self.model.dataChanged.connect(self._refresh_peaks_panel)
        self.model.rowsInserted.connect(self._refresh_peaks_panel)
        self.model.rowsRemoved.connect(self._refresh_peaks_panel)
        self.model.visibility_changed.connect(self._refresh_peaks_panel)

        self.plot = SpectrumPlotWidget(self)
        self.plot.cursor_moved.connect(self._on_cursor_moved)
        self.plot.pick_requested.connect(self._on_plot_pick)
        self.details_panel = DetailsPanel()
        self.peaks_panel = PeaksPanel()
        self.peaks_panel.remove_requested.connect(self._on_remove_manual_peak)
        # Simple mode's own ~260px side panel -- see _PeaksSidePanel below --
        # is a second, independent PeaksPanel instance kept in sync with
        # this one (both show the same selected spectrum's peaks).
        self.simple_peaks_panel = PeaksPanel()
        self.simple_peaks_panel.remove_requested.connect(self._on_remove_manual_peak)

        # One instrument/collection state, shared by Advanced mode's Collect
        # dock and Simple mode's top bar -- see collect_controller.py.
        self.collect_controller = CollectController(self)

        plot_container = _PlotAspectContainer(self.plot, self)
        self._central = _CentralArea(plot_container, self.peaks_panel, self.details_panel, self)

        self._build_list_dock()
        self._build_collect_dock()
        self._build_library_dock()
        self._build_toolbar()
        self._build_menus()

        self.simple_top_bar = SimpleTopBar(
            self.collect_controller,
            get_auto_label=lambda: self.auto_label_action.isChecked(),
            set_auto_label=lambda checked: self.auto_label_action.setChecked(checked),
            get_peak_label_params=self._peak_label_params,
            set_peak_label_params=self._set_peak_label_params,
            parent=self,
        )
        self.simple_top_bar.spectrum_collected.connect(self._on_spectrum_collected)
        self.simple_top_bar.preview_ready.connect(self._on_preview_ready)
        self.simple_top_bar.status_message.connect(self.statusBar().showMessage)
        self.simple_top_bar.advanced_requested.connect(lambda: self._apply_mode("advanced"))

        self.simple_bottom_bar = SimpleBottomBar(self.model, self.collect_controller, self)
        self.simple_bottom_bar.select_row_requested.connect(
            lambda row: self.list_view.setCurrentIndex(self.model.index(row))
        )
        self.simple_bottom_bar.visibility_toggle_requested.connect(self._toggle_visibility)
        self.simple_bottom_bar.rename_requested.connect(self._rename_current)
        self.simple_bottom_bar.recolor_requested.connect(self._recolor_current)
        self.simple_bottom_bar.save_requested.connect(self._on_save_selected)
        self.simple_bottom_bar.delete_requested.connect(self._delete_row)
        self.simple_bottom_bar.open_requested.connect(self._on_open)
        self.simple_bottom_bar.export_requested.connect(self._on_save_selected)

        self.simple_settings_strip = SimpleSettingsStrip(self.collect_controller, self.simple_top_bar, self)
        self.simple_plot_toolbar = _SimplePlotToolbar(
            self.pick_peaks_action, self.auto_label_action, self.clear_manual_peaks_action, self
        )
        self.simple_peaks_side_panel = _PeaksSidePanel(self.simple_peaks_panel, self)

        self._simple_central = _SimpleCentralArea(
            plot_container,
            self.simple_top_bar,
            self.simple_settings_strip,
            self.simple_plot_toolbar,
            self.simple_peaks_side_panel,
            self.simple_bottom_bar,
            self,
        )

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._central)
        self._stack.addWidget(self._simple_central)
        self.setCentralWidget(self._stack)

        self.statusBar().showMessage("Ready")
        self._update_recent_files_menu()
        self._apply_window_geometry()

        self._mode: str | None = None
        self._dock_state = None
        initial_mode = self.settings.value("ui/mode", "simple", type=str)
        if initial_mode not in ("simple", "advanced"):
            initial_mode = "simple"
        self._apply_mode(initial_mode)

    # -- docks ------------------------------------------------------------------

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
        self.spectra_dock = dock

    def _build_collect_dock(self) -> None:
        dock = QDockWidget("Collect", self)
        dock.setObjectName("collectDock")
        self.collect_panel = CollectPanel(controller=self.collect_controller)
        self.collect_panel.spectrum_collected.connect(self._on_spectrum_collected)
        self.collect_panel.preview_ready.connect(self._on_preview_ready)
        self.collect_panel.status_message.connect(self.statusBar().showMessage)
        dock.setWidget(self.collect_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.collect_dock = dock

    def _build_library_dock(self) -> None:
        dock = QDockWidget("Library", self)
        dock.setObjectName("libraryDock")
        self.library_panel = LibraryPanel(get_selected=self._get_selected_name_spectrum)
        self.library_panel.overlay_requested.connect(self._on_library_overlay)
        dock.setWidget(self.library_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.tabifyDockWidget(dock, self.collect_dock)
        self.library_dock = dock
        self._docks = [self.spectra_dock, self.collect_dock, self.library_dock]

    # -- toolbar / menus ----------------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)
        self.toolbar = toolbar

        self.stack_action = QAction("Stack/Offset", self)
        self.stack_action.setCheckable(True)
        self.stack_action.toggled.connect(self._on_stack_toggled)
        toolbar.addAction(self.stack_action)

        # Manual peak picking (click-to-add/remove on the plot): one shared
        # checkable action so Simple mode's plot toolbar, this toolbar and
        # the Process menu (see _build_menus) always agree on the state --
        # see openftir.gui.plot.SpectrumPlotWidget.set_pick_mode and
        # MainWindow._on_plot_pick.
        toolbar.addSeparator()
        self.pick_peaks_action = QAction("Pick peaks", self)
        self.pick_peaks_action.setCheckable(True)
        self.pick_peaks_action.setShortcut("P")
        self.pick_peaks_action.setToolTip("Click the plot to add or remove a manual peak (P)")
        self.pick_peaks_action.toggled.connect(self._on_pick_peaks_toggled)
        toolbar.addAction(self.pick_peaks_action)

        self.clear_manual_peaks_action = QAction("Clear manual peaks", self)
        self.clear_manual_peaks_action.setToolTip("Clear manual peaks of the selected spectrum")
        self.clear_manual_peaks_action.triggered.connect(self._on_clear_manual_peaks)
        toolbar.addAction(self.clear_manual_peaks_action)

        # Escape always leaves pick mode while this window (not some modal
        # dialog, which gets its own Escape-to-close behaviour) is active,
        # even if some other widget -- a spin box, the spectrum list -- has
        # focus. QShortcut's default context (WindowShortcut) gives us that.
        pick_escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        pick_escape.activated.connect(self._exit_pick_mode)

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

        process_menu.addSeparator()
        # Same QAction instances as the toolbar (see _build_toolbar) -- a
        # QAction can live in several menus/toolbars at once and always
        # agrees on its checked state.
        process_menu.addAction(self.pick_peaks_action)
        process_menu.addAction(self.clear_manual_peaks_action)

        view_menu = self.menuBar().addMenu("&View")

        self.auto_label_action = QAction("Auto-label peaks", self)
        self.auto_label_action.setCheckable(True)
        auto_label_default = bool(self.settings.value("view/autoLabelPeaks", True, type=bool))
        self.auto_label_action.setChecked(auto_label_default)
        self.auto_label_action.toggled.connect(self._on_auto_label_toggled)
        view_menu.addAction(self.auto_label_action)

        peak_label_settings_action = QAction("Peak labelling settings...", self)
        peak_label_settings_action.triggered.connect(self._on_peak_label_settings)
        view_menu.addAction(peak_label_settings_action)

        view_menu.addSeparator()

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.simple_mode_action = QAction("Simple mode", self)
        self.simple_mode_action.setCheckable(True)
        self.simple_mode_action.triggered.connect(lambda: self._apply_mode("simple"))
        self.advanced_mode_action = QAction("Advanced mode", self)
        self.advanced_mode_action.setCheckable(True)
        self.advanced_mode_action.triggered.connect(lambda: self._apply_mode("advanced"))
        mode_group.addAction(self.simple_mode_action)
        mode_group.addAction(self.advanced_mode_action)
        view_menu.addAction(self.simple_mode_action)
        view_menu.addAction(self.advanced_mode_action)

        # Sync the plot (constructed with its own built-in defaults, before
        # this settings-backed action existed) to whatever was actually
        # remembered -- setChecked() above fires no signal since toggled
        # was connected afterwards.
        self.plot.set_auto_label_enabled(auto_label_default)
        self.plot.set_auto_label_params(**self._peak_label_params())

        help_menu = self.menuBar().addMenu("&Help")

        probe_action = QAction("Probe Instrument Connection...", self)
        probe_action.triggered.connect(self._on_probe_instrument)
        help_menu.addAction(probe_action)

        help_menu.addSeparator()

        about_action = QAction("About OpenFTIR", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # -- Simple <-> Advanced mode -------------------------------------------------------

    def _apply_mode(self, mode: str) -> None:
        """Switch the central widget, and (for Advanced) the docks/toolbar,
        between Simple and Advanced mode. The plot widget, the spectrum
        model and the collect controller are never recreated -- only the
        views over them change -- see the module docstring."""
        if mode not in ("simple", "advanced"):
            mode = "simple"
        if mode == self._mode:
            return

        if mode == "simple":
            if self._mode != "simple":
                self._dock_state = self.saveState()
            for dock in self._docks:
                dock.hide()
            self.toolbar.hide()
            self._stack.setCurrentWidget(self._simple_central)
            self._simple_central.activate()
            self.collect_panel.set_active(False)
            self.simple_top_bar.set_active(True)
            self.simple_settings_strip.set_active(True)
            self.simple_bottom_bar.set_active(True)
        else:
            self._stack.setCurrentWidget(self._central)
            self._central.activate()
            # Let restoreState alone decide dock visibility/tabbing. Showing
            # tabified docks one by one *and* restoring made Qt build a new
            # tab bar each time, leaving stale duplicates on screen.
            if self._dock_state is not None:
                self.restoreState(self._dock_state)
            else:
                for dock in self._docks:
                    dock.show()
            self.toolbar.show()
            self.collect_panel.set_active(True)
            self.simple_top_bar.set_active(False)
            self.simple_settings_strip.set_active(False)
            self.simple_bottom_bar.set_active(False)

        self._mode = mode
        self.settings.setValue("ui/mode", mode)
        self.simple_mode_action.setChecked(mode == "simple")
        self.advanced_mode_action.setChecked(mode == "advanced")

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
        # A new selection invalidates any manual peak-pick result shown for
        # the previous spectrum: drop its markers and let the Peaks table
        # fall back to auto-labelling again.
        self._manual_peak_pick_active = False
        self.plot.clear_peaks()
        self._refresh_plot()
        if current.isValid():
            entry = self.model.entry_at(current.row())
            self.details_panel.show_spectrum(entry.name, entry.spectrum)
        else:
            self.details_panel.clear()
        self._refresh_peaks_panel()
        bottom_bar = getattr(self, "simple_bottom_bar", None)
        if bottom_bar is not None:
            bottom_bar.set_highlighted_row(self._current_row())

    def _refresh_plot(self, *_args) -> None:
        self.plot.refresh(self.model.entries(), highlight_row=self._current_row())

    # -- auto peak labelling --------------------------------------------------------------

    def _peak_label_params(self) -> dict:
        return {
            "max_transmittance": float(self.settings.value("peakLabel/maxTransmittance", 85.0, type=float)),
            "min_wavenumber": float(self.settings.value("peakLabel/minWavenumber", 1500.0, type=float)),
            "min_prominence_t": float(self.settings.value("peakLabel/minProminenceT", 2.0, type=float)),
        }

    def _on_auto_label_toggled(self, checked: bool) -> None:
        self.settings.setValue("view/autoLabelPeaks", checked)
        self.plot.set_auto_label_enabled(checked)
        self._refresh_peaks_panel()

    def _set_peak_label_params(self, new_params: dict) -> None:
        self.settings.setValue("peakLabel/maxTransmittance", new_params["max_transmittance"])
        self.settings.setValue("peakLabel/minWavenumber", new_params["min_wavenumber"])
        self.settings.setValue("peakLabel/minProminenceT", new_params["min_prominence_t"])
        self.plot.set_auto_label_params(**new_params)
        self._refresh_peaks_panel()

    def _on_peak_label_settings(self) -> None:
        params = self._peak_label_params()
        dialog = PeakLabelSettingsDialog(
            max_transmittance=params["max_transmittance"],
            min_wavenumber=params["min_wavenumber"],
            min_prominence_t=params["min_prominence_t"],
            parent=self,
        )
        if not dialog.exec():
            return
        self._set_peak_label_params(dialog.params())

    def _refresh_peaks_panel(self, *_args) -> None:
        """Fill both Peaks tables (Advanced mode's tab and Simple mode's
        side panel -- two independent PeaksPanel instances, kept in sync
        here) with the selected spectrum's auto-labelled peaks merged with
        its manually picked ones, sorted by descending wavenumber.

        Manual peaks always show, even with auto-labelling off; an auto
        peak within 4 cm-1 of a manual one is dropped (the manual one
        wins) -- same rule as the plot's own markers, see
        openftir.gui.plot.SpectrumPlotWidget.refresh.

        Left alone (both tables keep showing whatever they already have)
        while a manual Peak Picking dialog result is currently active --
        see _on_peak_picking.
        """
        if self._manual_peak_pick_active:
            return
        row = self._current_row()
        if row is None:
            self.peaks_panel.clear()
            self.simple_peaks_panel.clear()
            return
        entry = self.model.entry_at(row)
        spectrum = entry.spectrum
        value_label = {YUnit.TRANSMITTANCE: "%T", YUnit.ABSORBANCE: "Absorbance", YUnit.REFLECTANCE: "%R"}.get(spectrum.y_unit, "Value")
        for panel in self.findChildren(PeaksPanel):
            panel.set_value_label(value_label)

        manual_positions = list(entry.manual_peaks)
        rows: list[tuple[Peak, str]] = []
        for position in manual_positions:
            idx = int(np.argmin(np.abs(spectrum.x - position))) if len(spectrum.x) else None
            value = float(spectrum.y[idx]) if idx is not None else 0.0
            rows.append((Peak(position=position, height=value), "manual"))

        if self.auto_label_action.isChecked():
            try:
                peaks = processing.auto_label_peaks(spectrum, **self._peak_label_params())
            except ValueError:
                peaks = []  # single-beam/interferogram/etc.
            for p in peaks:
                if any(abs(p.position - m) <= 4.0 for m in manual_positions):
                    continue
                rows.append((p, "auto"))

        rows.sort(key=lambda r: r[0].position, reverse=True)
        self.peaks_panel.set_rows(rows)
        self.simple_peaks_panel.set_rows(rows)

    # -- manual peak picking (Simple + Advanced) -------------------------------------------

    def _on_pick_peaks_toggled(self, checked: bool) -> None:
        self.plot.set_pick_mode(checked)

    def _exit_pick_mode(self) -> None:
        self.pick_peaks_action.setChecked(False)

    def _on_clear_manual_peaks(self) -> None:
        row = self._current_row()
        if row is None:
            return
        self.model.set_manual_peaks(row, [])

    def _on_remove_manual_peak(self, position: float) -> None:
        row = self._current_row()
        if row is None:
            return
        entry = self.model.entry_at(row)
        remaining = [p for p in entry.manual_peaks if abs(p - position) > 1e-6]
        self.model.set_manual_peaks(row, remaining)

    def _value_at(self, spectrum: Spectrum, x: float) -> float | None:
        """Linearly interpolate ``spectrum.y`` at ``x`` (``spectrum.x`` may
        be ascending or descending -- see Spectrum's docstring). ``None``
        if ``x`` falls outside the spectrum's range or it has no points."""
        if len(spectrum.x) == 0:
            return None
        order = np.argsort(spectrum.x)
        xa, ya = spectrum.x[order], spectrum.y[order]
        if x < xa[0] or x > xa[-1]:
            return None
        return float(np.interp(x, xa, ya))

    def _snap_extremum(self, spectrum: Spectrum, x: float, x_tolerance: float) -> float | None:
        """The x position of the local extremum (minimum for %T/
        reflectance, maximum for absorbance) within ``x_tolerance`` of
        ``x``, or ``None`` if the unit isn't one of those (single-beam,
        interferogram, ... are ignored) or no point falls in range."""
        if spectrum.y_unit in (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE):
            down = True
        elif spectrum.y_unit == YUnit.ABSORBANCE:
            down = False
        else:
            return None
        lo, hi = sorted((x - x_tolerance, x + x_tolerance))
        mask = (spectrum.x >= lo) & (spectrum.x <= hi)
        if not np.any(mask):
            return None
        xs, ys = spectrum.x[mask], spectrum.y[mask]
        idx = int(np.argmin(ys)) if down else int(np.argmax(ys))
        return float(xs[idx])

    def _pick_target_row(self, x: float, y: float, visible: list[tuple[int, object]], px_h: float) -> int | None:
        """Which visible spectrum a click at data (x, y) targets: the
        selected one if it's visible, else whichever visible trace passes
        closest to (x, y) in screen pixels (vertically, at that x)."""
        current = self._current_row()
        if current is not None:
            entry = self.model.entry_at(current)
            if entry.visible:
                return current

        best_row: int | None = None
        best_dist: float | None = None
        for row, entry in visible:
            value = self._value_at(entry.spectrum, x)
            if value is None:
                continue
            dist = abs((y - value) / px_h)
            if best_dist is None or dist < best_dist:
                best_dist, best_row = dist, row
        return best_row

    def _on_plot_pick(self, x: float, y: float) -> None:
        """Handle a left click on the plot while pick-peaks mode is on (see
        openftir.gui.plot.SpectrumPlotWidget.pick_requested): clicking near
        an existing manual peak marker (within ~8 screen px) removes it;
        otherwise snap to the nearest local extremum within +/-12 px (in x)
        and add it as a new manual peak."""
        entries = self.model.entries()
        visible = [(i, e) for i, e in enumerate(entries) if e.visible]
        if not visible:
            return
        px_w, px_h = self.plot.pixel_size()
        px_w = abs(px_w) or 1.0
        px_h = abs(px_h) or 1.0

        target_row = self._pick_target_row(x, y, visible, px_h)
        if target_row is None:
            return
        entry = self.model.entry_at(target_row)

        for peak_x in entry.manual_peaks:
            peak_y = self._value_at(entry.spectrum, peak_x)
            if peak_y is None:
                continue
            dist_px = ((x - peak_x) / px_w) ** 2 + ((y - peak_y) / px_h) ** 2
            if dist_px <= 8.0**2:
                remaining = [p for p in entry.manual_peaks if p != peak_x]
                self.model.set_manual_peaks(target_row, remaining)
                return

        new_x = self._snap_extremum(entry.spectrum, x, 12.0 * px_w)
        if new_x is None or new_x in entry.manual_peaks:
            return
        self.model.set_manual_peaks(target_row, [*entry.manual_peaks, new_x])

    # -- list dock / spectrum chip actions -----------------------------------------------

    def _rename_current(self, row: int | None = None) -> None:
        if row is None:
            row = self._current_row()
        if row is None:
            return
        entry = self.model.entry_at(row)
        name, ok = QInputDialog.getText(self, "Rename Spectrum", "New name:", text=entry.name)
        if ok and name.strip():
            self.model.setData(self.model.index(row), name.strip(), Qt.EditRole)

    def _recolor_current(self, row: int | None = None) -> None:
        if row is None:
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

    def _delete_row(self, row: int) -> None:
        self.model.remove(row)

    def _toggle_visibility(self, row: int) -> None:
        entry = self.model.entry_at(row)
        new_state = Qt.Unchecked if entry.visible else Qt.Checked
        self.model.setData(self.model.index(row), new_state, Qt.CheckStateRole)

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
                # Single-spectrum files are named after the file (OMNIC titles are
                # often just a timestamp); groups use each spectrum's own title.
                name = stem if len(spectra) == 1 else (spectrum.title or f"{stem} [{i + 1}]")
                # Restore manual peaks a .ofjson save mirrored into meta
                # (see SpectrumListModel.add); any other format just has
                # no such key, so manual_peaks comes back empty.
                manual_peaks = [float(p) for p in spectrum.meta.get("manual_peaks", []) or []]
                self.model.add(spectrum, name=name, manual_peaks=manual_peaks)
                loaded_any = True
            if spectra:
                self._add_recent_file(path)

        if loaded_any:
            self.plot.full_scale()
        if errors:
            QMessageBox.critical(self, "Open", "Some files could not be opened:\n\n" + "\n".join(errors))

    def _on_save_selected(self, row: int | None = None) -> None:
        if row is None:
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
        accepted = dialog.exec()
        if accepted:
            self._manual_peak_pick_active = True
        else:
            self._manual_peak_pick_active = False
            self.plot.clear_peaks()
            self._refresh_peaks_panel()

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

    # -- collect panel / simple top bar callbacks --------------------------------------

    def _on_spectrum_collected(self, spectrum: Spectrum, name: str) -> None:
        self.plot.clear_live_preview()
        if spectrum.y_unit == YUnit.SINGLE_BEAM:
            # A background: kept in the list (Advanced mode can tick it on to
            # inspect it) but hidden, so it doesn't share an axis with %T data.
            self.model.add(spectrum, name=name, visible=False)
            return
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

        # restoreGeometry() silently moves an off-screen window back on
        # screen, so decide from the rectangle we saved ourselves whether the
        # previous geometry still fits (e.g. an external monitor is gone).
        saved = self.settings.value("windowGeometry")
        saved_rect = self.settings.value("windowRect")
        if (
            saved
            and isinstance(saved_rect, QRect)
            and available.contains(saved_rect)
            and self.restoreGeometry(saved)
            and available.contains(self.frameGeometry())
        ):
            return

        width = max(1, int(available.width() * 0.9))
        height = max(1, int(available.height() * 0.9))
        x = available.x() + (available.width() - width) // 2
        y = available.y() + (available.height() - height) // 2
        self.setGeometry(x, y, width, height)

    def closeEvent(self, event) -> None:
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowRect", self.frameGeometry())
        super().closeEvent(event)
