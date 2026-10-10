"""Simple mode's top bar (instrument + connect + Background/Collect sample),
settings strip (scans/resolution/range/format, always visible and editable
inline), and bottom bar (spectrum chips + Export) -- all thin views over the
shared :class:`~openftir.gui.collect_controller.CollectController` and the
shared :class:`~openftir.gui.spectrum_list.SpectrumListModel`, exactly like
Advanced mode's ``CollectPanel`` dock and spectra list dock, just laid out
for a single-screen, minimal workflow.
"""

from __future__ import annotations

from openftir.spectrum import YUnit

from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from openftir.gui.collect_controller import (
    CollectController,
    DEFAULT_SCANS,
    FORMATS,
    SIMULATED_SAMPLES,
)
from openftir.gui.dialogs.simple_settings_dialog import SimpleSettingsDialog
from openftir.gui.spectrum_list import SpectrumListModel

_PILL_COLORS = {
    "connected": ("#2ca02c", "white"),
    "disconnected": ("#d6d6d6", "#333333"),
    "busy": ("#2a82da", "white"),
}
_PILL_STYLE = (
    "QLabel {{ border-radius: 10px; padding: 3px 14px;"
    " font-weight: 500; background-color: {bg}; color: {fg}; }}"
)


class SimpleTopBar(QWidget):
    """Title, instrument picker + Connect/Disconnect + status pill,
    Background/Collect sample/Advanced, plus a thin progress bar that
    appears under the row during a collection."""

    #: spectrum, chosen name -- mirrors CollectPanel.spectrum_collected
    spectrum_collected = Signal(object, str)
    preview_ready = Signal(object, str)
    status_message = Signal(str)
    advanced_requested = Signal()

    def __init__(
        self,
        controller: CollectController,
        get_auto_label: Callable[[], bool],
        set_auto_label: Callable[[bool], None],
        get_peak_label_params: Callable[[], dict],
        set_peak_label_params: Callable[[dict], None],
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self._get_auto_label = get_auto_label
        self._set_auto_label = set_auto_label
        self._get_peak_label_params = get_peak_label_params
        self._set_peak_label_params = set_peak_label_params
        self._active = True
        self._busy_mode: str | None = None
        self._pending_after_connect: str | None = None

        # An explicit opaque background: this bar sits directly over
        # Advanced mode's docks/toolbar area once they're hidden, and should
        # always read as a clean, solid strip rather than a transparent one.
        self.setAutoFillBackground(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("OpenFTIR")
        title.setStyleSheet("font-size: 15pt; font-weight: 600;")
        row.addWidget(title)
        row.addSpacing(8)

        self.instrument_combo = QComboBox()
        self.instrument_combo.setMinimumWidth(150)
        self.instrument_combo.currentIndexChanged.connect(self._on_instrument_combo_changed)
        row.addWidget(self.instrument_combo)

        self.sim_sample_combo = QComboBox()
        self.sim_sample_combo.addItems(SIMULATED_SAMPLES)
        self.sim_sample_combo.currentTextChanged.connect(self._on_sim_sample_changed)
        row.addWidget(self.sim_sample_combo)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        row.addWidget(self.connect_btn)

        self.pill = QLabel("Not connected")
        row.addWidget(self.pill)

        row.addStretch(1)

        self.background_btn = QPushButton("Background")
        self.background_btn.clicked.connect(self._on_background_clicked)
        row.addWidget(self.background_btn)

        self.collect_btn = QPushButton("Collect sample")
        self.collect_btn.setObjectName("accentButton")
        self.collect_btn.clicked.connect(self._on_collect_clicked)
        row.addWidget(self.collect_btn)

        self.advanced_link = QPushButton("Advanced")
        self.advanced_link.setFlat(True)
        self.advanced_link.setCursor(Qt.PointingHandCursor)
        self.advanced_link.setStyleSheet("color: #2a82da;")
        self.advanced_link.clicked.connect(self.advanced_requested.emit)
        row.addWidget(self.advanced_link)

        outer.addLayout(row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setVisible(False)
        outer.addWidget(self.progress_bar)

        self.setStyleSheet(
            "QPushButton#accentButton {"
            " background-color: #2a82da; color: white; font-weight: 600;"
            " border: none; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton#accentButton:disabled { background-color: #aecbe8; color: #eef4fb; }"
            "QPushButton#accentButton:hover:!disabled { background-color: #3a8fe6; }"
        )

        self._populate_instrument_combo()

        controller.connection_changed.connect(self._on_connection_changed)
        controller.settings_changed.connect(self._on_settings_changed)
        controller.progress.connect(self._on_progress)
        controller.collect_started.connect(self._on_collect_started)
        controller.collect_finished.connect(self._on_collect_finished)
        controller.collect_cancelled.connect(self._on_collect_cancelled)
        controller.collect_failed.connect(self._on_collect_failed)
        controller.preview_ready.connect(self._on_preview_ready)
        controller.status_message.connect(self._on_status_message)
        controller.error.connect(self._on_error)

        self._refresh_idle_ui()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(900, 54)

    # -- Simple <-> Advanced mode switch ------------------------------------------------

    def set_active(self, active: bool) -> None:
        self._active = active
        if not active:
            return
        if self.controller.busy:
            self._apply_busy_ui()
        else:
            self._refresh_idle_ui()

    # -- instrument picker --------------------------------------------------------------

    def _populate_instrument_combo(self) -> None:
        names = self.controller.driver_display_names()
        self.instrument_combo.blockSignals(True)
        self.instrument_combo.clear()
        for driver_id in self.controller.available_drivers():
            self.instrument_combo.addItem(names.get(driver_id, driver_id), driver_id)
        idx = self.instrument_combo.findData(self.controller.driver_id)
        if idx >= 0:
            self.instrument_combo.setCurrentIndex(idx)
        self.instrument_combo.blockSignals(False)
        self._update_sim_sample_visibility()

    def _update_sim_sample_visibility(self) -> None:
        self.sim_sample_combo.setVisible(self.instrument_combo.currentData() == "simulated")

    def _on_instrument_combo_changed(self, _index: int) -> None:
        driver_id = self.instrument_combo.currentData()
        self._update_sim_sample_visibility()
        if driver_id:
            self.controller.set_driver(driver_id)

    def _on_sim_sample_changed(self, name: str) -> None:
        self.controller.set_sim_sample(name)

    # -- status pill / connect --------------------------------------------------------

    def _set_pill(self, text: str, state: str) -> None:
        self.pill.setText(text)
        bg, fg = _PILL_COLORS[state]
        self.pill.setStyleSheet(_PILL_STYLE.format(bg=bg, fg=fg))

    def _on_connect_clicked(self) -> None:
        if self.controller.busy:
            return
        if self.controller.connected:
            self.controller.disconnect_instrument()
            return
        self._try_connect()

    def _try_connect(self) -> None:
        if not self.controller.available_drivers():
            return
        self._set_pill("Connecting…", "busy")
        self.connect_btn.setEnabled(False)
        self.controller.connect_instrument()

    def _on_connection_changed(self, connected: bool, _info) -> None:
        if connected and self._pending_after_connect:
            mode = self._pending_after_connect
            self._pending_after_connect = None
            self.controller.start_collect(mode)
        else:
            self._pending_after_connect = None
        if self._active and not self.controller.busy:
            self._refresh_idle_ui()

    def _on_settings_changed(self) -> None:
        if not self._active:
            return
        idx = self.instrument_combo.findData(self.controller.driver_id)
        if idx >= 0 and idx != self.instrument_combo.currentIndex():
            self.instrument_combo.blockSignals(True)
            self.instrument_combo.setCurrentIndex(idx)
            self.instrument_combo.blockSignals(False)
            self._update_sim_sample_visibility()

    def _refresh_idle_ui(self) -> None:
        connected = self.controller.connected
        info = self.controller.info
        if connected:
            model = info.model if info is not None else self.controller.driver_id
            self._set_pill(f"Connected · {model}", "connected")
        else:
            self._set_pill("Not connected", "disconnected")
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.connect_btn.setEnabled(True)
        self.instrument_combo.setEnabled(not connected)
        self.collect_btn.setEnabled(connected)
        self.background_btn.setEnabled(True)
        self.background_btn.setText("Background")
        self.collect_btn.setText("Collect sample")
        self.progress_bar.setVisible(False)

    # -- Background / Collect sample -------------------------------------------------

    def _on_background_clicked(self) -> None:
        if self.controller.busy:
            if self._busy_mode == "background":
                self.controller.cancel()
            return
        if not self.controller.connected:
            self._pending_after_connect = "background"
            self._try_connect()
            return
        self.controller.start_collect("background")

    def _on_collect_clicked(self) -> None:
        if self.controller.busy:
            if self._busy_mode == "sample":
                self.controller.cancel()
            return
        self.controller.start_collect("sample")

    def _on_collect_started(self, mode: str) -> None:
        self._busy_mode = mode
        if self._active:
            self._apply_busy_ui()

    def _apply_busy_ui(self) -> None:
        if self._busy_mode == "background":
            self.background_btn.setText("Cancel")
            self.background_btn.setEnabled(True)
            self.collect_btn.setEnabled(False)
        else:
            self.collect_btn.setText("Cancel")
            self.collect_btn.setEnabled(True)
            self.background_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.instrument_combo.setEnabled(False)
        self._set_pill("Collecting…", "busy")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, max(self.controller.scans, 1))
        self.progress_bar.setValue(0)

    def _on_progress(self, done: int, total: int) -> None:
        if not self._active:
            return
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self._set_pill(f"Collecting… {done}/{total}", "busy")

    def _end_busy(self) -> None:
        self._busy_mode = None
        if self._active:
            self._refresh_idle_ui()

    def _on_collect_finished(self, mode: str, spectrum, suggested_name: str) -> None:
        self._end_busy()
        if not self._active:
            return
        name = self.controller.next_sample_name() if mode == "sample" else suggested_name
        self.spectrum_collected.emit(spectrum, name)

    def _on_collect_cancelled(self, _mode: str) -> None:
        self._end_busy()

    def _on_collect_failed(self, _mode: str, _message: str) -> None:
        self._end_busy()

    def _on_preview_ready(self, spectrum, label: str) -> None:
        if self._active:
            self.preview_ready.emit(spectrum, label)

    def _on_status_message(self, message: str) -> None:
        if self._active:
            self.status_message.emit(message)

    def _on_error(self, title: str, message: str) -> None:
        if self._active:
            QMessageBox.critical(self, title, message)

    # -- settings -------------------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        """Used by SimpleSettingsStrip's "More settings..." button."""
        dialog = SimpleSettingsDialog(
            self.controller,
            self._get_auto_label,
            self._set_auto_label,
            self._get_peak_label_params,
            self._set_peak_label_params,
            parent=self,
        )
        dialog.exec()


class SimpleSettingsStrip(QWidget):
    """Row 2: a compact, always-visible, inline-editable strip of the
    acquisition settings a user actually tweaks often (scans, resolution,
    range, format) plus the background's age and a "More settings..."
    button for everything else (apodization, diagnostics, background
    save/load, auto peak-labelling thresholds -- see
    ``openftir.gui.dialogs.simple_settings_dialog``).

    A thin view over the shared CollectController, exactly like the top
    bar -- see that class's docstring.
    """

    def __init__(self, controller: CollectController, top_bar: SimpleTopBar, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._top_bar = top_bar
        self._active = True

        self.setAutoFillBackground(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 2, 14, 8)
        row.setSpacing(10)

        row.addWidget(QLabel("Scans:"))
        self.scans_spin = QSpinBox()
        self.scans_spin.setRange(1, 10000)
        self.scans_spin.setValue(DEFAULT_SCANS)
        self.scans_spin.valueChanged.connect(lambda v: controller.update_params(scans=v))
        row.addWidget(self.scans_spin)

        row.addWidget(QLabel("Resolution:"))
        self.resolution_combo = QComboBox()
        self._set_resolutions((4.0, 8.0, 16.0, 2.0), default=4.0)
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_combo_changed)
        row.addWidget(self.resolution_combo)

        row.addWidget(QLabel("Range:"))
        self.x_min_spin = QDoubleSpinBox()
        self.x_min_spin.setRange(0, 20000)
        self.x_min_spin.setDecimals(0)
        self.x_min_spin.setValue(controller.x_min)
        self.x_min_spin.valueChanged.connect(lambda v: controller.update_params(x_min=v))
        row.addWidget(self.x_min_spin)
        row.addWidget(QLabel("to"))
        self.x_max_spin = QDoubleSpinBox()
        self.x_max_spin.setRange(0, 20000)
        self.x_max_spin.setDecimals(0)
        self.x_max_spin.setValue(controller.x_max)
        self.x_max_spin.valueChanged.connect(lambda v: controller.update_params(x_max=v))
        row.addWidget(self.x_max_spin)

        row.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(list(FORMATS))
        self.format_combo.currentTextChanged.connect(lambda t: controller.update_params(final_format=t))
        row.addWidget(self.format_combo)

        row.addStretch(1)

        self.background_label = QLabel("No background")
        row.addWidget(self.background_label)

        self.more_settings_btn = QPushButton("More settings…")
        self.more_settings_btn.clicked.connect(top_bar.open_settings_dialog)
        row.addWidget(self.more_settings_btn)

        controller.settings_changed.connect(self._refresh_settings_ui)
        controller.background_changed.connect(self._update_background_label)

        self._age_timer = QTimer(self)
        self._age_timer.setInterval(30_000)
        self._age_timer.timeout.connect(self._update_background_label)
        self._age_timer.start()

        self._refresh_settings_ui()
        self._update_background_label()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(900, 34)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._refresh_settings_ui()
            self._update_background_label()

    # -- settings refresh ---------------------------------------------------------------

    def _set_resolutions(self, resolutions: tuple[float, ...], default: float) -> None:
        self.resolution_combo.blockSignals(True)
        self.resolution_combo.clear()
        for r in resolutions:
            self.resolution_combo.addItem(f"{r:g}", r)
        idx = self.resolution_combo.findData(default)
        if idx >= 0:
            self.resolution_combo.setCurrentIndex(idx)
        self.resolution_combo.blockSignals(False)

    def _on_resolution_combo_changed(self, _index: int) -> None:
        value = self.resolution_combo.currentData()
        if value is not None:
            self.controller.update_params(resolution=float(value))

    def _refresh_settings_ui(self) -> None:
        if not self._active:
            return
        c = self.controller
        widgets = (self.scans_spin, self.resolution_combo, self.x_min_spin, self.x_max_spin, self.format_combo)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.scans_spin.setValue(c.scans)
            self._set_resolutions(c.resolutions, c.resolution)
            self.x_min_spin.setValue(c.x_min)
            self.x_max_spin.setValue(c.x_max)
            idx = self.format_combo.findText(c.final_format)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
        finally:
            for w in widgets:
                w.blockSignals(False)

    # -- background age -------------------------------------------------------------------

    def _update_background_label(self) -> None:
        minutes = self.controller.background_age_minutes()
        if minutes is None:
            self.background_label.setText("No background")
            self.background_label.setStyleSheet("color: #777777;")
            return
        self.background_label.setText(f"Background: {minutes} min")
        stale = self.controller.background_is_stale()
        self.background_label.setStyleSheet("color: #c0392b; font-weight: 600;" if stale else "color: #666666;")


class SpectrumChip(QFrame):
    """One spectrum in the bottom bar: colour dot + name, click to
    select/highlight, an eye button to toggle visibility, right-click for
    Rename/Change colour/Save as/Delete."""

    clicked = Signal(int)
    visibility_toggled = Signal(int)
    rename_requested = Signal(int)
    recolor_requested = Signal(int)
    save_requested = Signal(int)
    delete_requested = Signal(int)

    def __init__(self, row: int, entry, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(9, 9)
        layout.addWidget(self._dot)

        self._name = QLabel()
        layout.addWidget(self._name)

        self._eye_btn = QToolButton()
        self._eye_btn.setAutoRaise(True)
        self._eye_btn.setFixedSize(18, 18)
        self._eye_btn.clicked.connect(lambda: self.visibility_toggled.emit(self.row))
        layout.addWidget(self._eye_btn)

        self.update_entry(entry)
        self._apply_style()

    def update_entry(self, entry) -> None:
        self._name.setText(entry.name)
        self._dot.setStyleSheet(f"background-color: {entry.color.name()}; border-radius: 4px;")
        self._eye_btn.setText("◉" if entry.visible else "○")
        self._eye_btn.setToolTip("Hide" if entry.visible else "Show")
        opacity = "color: #1a1a1a;" if entry.visible else "color: #aaaaaa;"
        self._name.setStyleSheet(opacity)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "SpectrumChip { background: #e3f0fc; border: 1px solid #2a82da; border-radius: 13px; }"
            )
        else:
            self.setStyleSheet(
                "SpectrumChip { background: #f1f1f1; border: 1px solid #e0e0e0; border-radius: 13px; }"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.row)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self.rename_requested.emit(self.row))
        menu.addAction("Change colour...", lambda: self.recolor_requested.emit(self.row))
        menu.addAction("Save as...", lambda: self.save_requested.emit(self.row))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_requested.emit(self.row))
        menu.exec(event.globalPos())


class BackgroundChip(QFrame):
    """Muted chip showing the background's age, e.g. 'Background · 12 min'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        self._label = QLabel("No background")
        layout.addWidget(self._label)
        self.setStyleSheet("BackgroundChip { background: #eaeaea; border-radius: 13px; }")

    def update_state(self, minutes: int | None, stale: bool) -> None:
        if minutes is None:
            self._label.setText("No background")
            self._label.setStyleSheet("color: #777777;")
            return
        self._label.setText(f"Background · {minutes} min")
        self._label.setStyleSheet("color: #c0392b; font-weight: 600;" if stale else "color: #666666;")


class SimpleBottomBar(QWidget):
    """Spectrum chips (+ the background chip and a '+ Open' chip) and Export."""

    select_row_requested = Signal(int)
    visibility_toggle_requested = Signal(int)
    rename_requested = Signal(int)
    recolor_requested = Signal(int)
    save_requested = Signal(int)
    delete_requested = Signal(int)
    open_requested = Signal()
    #: "PDF report..." (listed first/default) and "Data file..." (the
    #: existing save-to-disk flow) -- see MainWindow._on_export_pdf_report /
    #: _on_save_selected.
    export_pdf_requested = Signal()
    export_data_requested = Signal()

    def __init__(self, model: SpectrumListModel, controller: CollectController, parent=None):
        super().__init__(parent)
        self.model = model
        self.controller = controller
        self._chips: list[SpectrumChip] = []
        self._highlighted_row: int | None = None

        # See SimpleTopBar's own setAutoFillBackground(True) -- same reason.
        self.setAutoFillBackground(True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 10)
        outer.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(46)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setAutoFillBackground(True)
        self.scroll.viewport().setAutoFillBackground(True)

        self._chip_container = QWidget()
        self._chip_container.setAutoFillBackground(True)
        self._chip_layout = QHBoxLayout(self._chip_container)
        self._chip_layout.setContentsMargins(2, 2, 2, 2)
        self._chip_layout.setSpacing(6)
        self.scroll.setWidget(self._chip_container)
        outer.addWidget(self.scroll, 1)

        self.background_chip = BackgroundChip()

        self._open_chip = QPushButton("+ Open")
        self._open_chip.setFlat(True)
        self._open_chip.setCursor(Qt.PointingHandCursor)
        self._open_chip.setStyleSheet(
            "QPushButton { border: 1px dashed #bbbbbb; border-radius: 13px; padding: 3px 12px; color: #555555; }"
        )
        self._open_chip.clicked.connect(self.open_requested.emit)

        # A small dropdown rather than a plain button: "PDF report..." is
        # listed first (the common case for a finished sample), "Data
        # file..." is the original save-to-disk behaviour.
        self.export_btn = QToolButton(self)
        self.export_btn.setText("Export")
        self.export_btn.setPopupMode(QToolButton.InstantPopup)
        export_menu = QMenu(self.export_btn)
        pdf_action = QAction("PDF report…", self.export_btn)
        pdf_action.triggered.connect(self.export_pdf_requested.emit)
        export_menu.addAction(pdf_action)
        data_action = QAction("Data file…", self.export_btn)
        data_action.triggered.connect(self.export_data_requested.emit)
        export_menu.addAction(data_action)
        self.export_btn.setMenu(export_menu)
        outer.addWidget(self.export_btn, 0)

        self._age_timer = QTimer(self)
        self._age_timer.setInterval(60_000)
        self._age_timer.timeout.connect(self._update_background_chip)
        self._age_timer.start()
        controller.background_changed.connect(self._update_background_chip)

        model.rowsInserted.connect(lambda *_: self.rebuild())
        model.rowsRemoved.connect(lambda *_: self.rebuild())
        model.dataChanged.connect(lambda *_: self.rebuild())
        model.visibility_changed.connect(self.rebuild)

        self.rebuild()
        self._update_background_chip()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(900, 64)

    def set_active(self, active: bool) -> None:
        if active:
            self.rebuild()
            self._update_background_chip()

    # -- rebuilding the chip row ------------------------------------------------------

    def rebuild(self) -> None:
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.background_chip and widget is not self._open_chip:
                # hide() immediately -- deleteLater() alone leaves the old
                # chip paintable (and visible in a grab()) until the next
                # event loop pass actually destroys it.
                widget.hide()
                widget.deleteLater()
        self._chips.clear()

        self._chip_layout.addWidget(self.background_chip)
        for row, entry in enumerate(self.model.entries()):
            if entry.spectrum.y_unit == YUnit.SINGLE_BEAM:
                continue  # backgrounds are shown by the background-age chip
            chip = SpectrumChip(row, entry)
            chip.clicked.connect(self.select_row_requested.emit)
            chip.visibility_toggled.connect(self.visibility_toggle_requested.emit)
            chip.rename_requested.connect(self.rename_requested.emit)
            chip.recolor_requested.connect(self.recolor_requested.emit)
            chip.save_requested.connect(self.save_requested.emit)
            chip.delete_requested.connect(self.delete_requested.emit)
            chip.set_selected(row == self._highlighted_row)
            self._chip_layout.addWidget(chip)
            self._chips.append(chip)
        self._chip_layout.addWidget(self._open_chip)
        self._chip_layout.addStretch(1)

    def set_highlighted_row(self, row: int | None) -> None:
        self._highlighted_row = row
        for chip in self._chips:
            chip.set_selected(chip.row == row)

    def _update_background_chip(self) -> None:
        self.background_chip.update_state(
            self.controller.background_age_minutes(), self.controller.background_is_stale()
        )
