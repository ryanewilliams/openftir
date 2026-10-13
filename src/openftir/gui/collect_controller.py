"""Owns all instrument/collection state, shared by every view that needs it
(the Advanced mode :class:`~openftir.gui.collect_panel.CollectPanel` dock and
the Simple mode top/bottom bars -- see ``openftir.gui.simple_panels``).

Nothing here is a ``QWidget``: this is pure state + behaviour (the connected
:class:`~openftir.instruments.base.Instrument`, acquisition settings, the
background spectrum, the instrument lock and worker launching) plus Qt
signals so any number of views can stay in sync with it and with each other.
Keeping this out of the views means Simple and Advanced mode can never drift
apart on what "connected"/"the background"/"the current settings" mean --
there is exactly one answer, held here.

Naming a freshly collected sample is the one thing that is genuinely a
per-view decision (Advanced mode prompts with a dialog, Simple mode
auto-names "Sample N") so it is deliberately left to the caller: this class
only ever emits a *suggested* name alongside ``collect_finished``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QSettings, Signal

from openftir.instruments import registry
from openftir.instruments.base import CollectParams, Instrument, InstrumentInfo
from openftir.processing import ratio
from openftir.spectrum import local_naive, Spectrum, YUnit
from openftir.gui.workers import CallWorker, CollectWorker, start_worker

BACKGROUND_WARN_AFTER = timedelta(minutes=60)
SIMULATED_SAMPLES = ["polystyrene", "limonene", "water-vapour", "none"]

#: Per-scan delay for the simulated instrument in the app, so a collection
#: takes a realistic moment and the progress bar is visible (16 scans ≈ 1 s).
SIMULATED_SCAN_TIME_S = 0.06
# %Transmittance listed first so it's the default -- ATR-FTIR users normally
# work in %T. See _restore_driver_settings: the remembered-format settings
# key is "format_v2" so an old install's saved "Absorbance" (which used to
# be the default, not necessarily a deliberate choice) doesn't silently keep
# overriding the new %T default.
FORMATS = {"%Transmittance": YUnit.TRANSMITTANCE, "Absorbance": YUnit.ABSORBANCE}
DEFAULT_SCANS = 16
DEFAULT_RESOLUTIONS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
_LAST_DRIVER_KEY = "collect/lastDriver"


class CollectController(QObject):
    """Instrument connection, acquisition settings, background spectrum and
    collection worker lifecycle -- everything except the actual widgets."""

    #: connected, InstrumentInfo|None
    connection_changed = Signal(bool, object)
    #: scans_done, scans_total
    progress = Signal(int, int)
    #: mode ("background"/"sample")
    collect_started = Signal(str)
    #: mode, spectrum, suggested_name -- naming is left to the view, see module docstring
    collect_finished = Signal(str, object, str)
    collect_cancelled = Signal(str)
    #: mode, message
    collect_failed = Signal(str, str)
    #: a raw single-beam result to preview live (spectrum, label)
    preview_ready = Signal(object, str)
    background_changed = Signal()
    diagnostics_ready = Signal(object)
    #: title, message -- never raised, always surfaced through this signal
    error = Signal(str, str)
    status_message = Signal(str)
    #: driver id, acquisition settings, or resolutions changed
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instrument: Instrument | None = None
        self._info: InstrumentInfo | None = None
        self._background: Spectrum | None = None
        self._background_time: datetime | None = None
        self._worker: CollectWorker | None = None
        self._instrument_lock = threading.Lock()
        self._pending_mode: str | None = None
        self._busy = False
        self._settings = QSettings("OpenFTIR", "OpenFTIR")
        self._sample_counter = 0

        self.driver_id = ""
        self.sim_sample = SIMULATED_SAMPLES[0]
        self.scans = DEFAULT_SCANS
        self.resolution = 4.0
        self.resolutions: tuple[float, ...] = DEFAULT_RESOLUTIONS
        self.x_min = CollectParams.x_min
        self.x_max = CollectParams.x_max
        self.apodization = "happ-genzel"
        self.final_format = "%Transmittance"

        drivers = self.available_drivers()
        last = self._settings.value(_LAST_DRIVER_KEY, "", type=str)
        initial = last if last in drivers else (drivers[0] if drivers else "")
        if initial:
            self.set_driver(initial)

    # -- driver management -----------------------------------------------------------

    def available_drivers(self) -> list[str]:
        return sorted(registry.available_drivers())

    def driver_display_names(self) -> dict[str, str]:
        """``{driver_id: human-readable name}`` for every available driver,
        e.g. for an instrument-selection dropdown. Falls back to the id
        itself (title-cased) if a driver leaves ``display_name`` empty."""
        return {
            driver_id: (cls.display_name or driver_id.capitalize())
            for driver_id, cls in registry.available_drivers().items()
        }

    @property
    def connected(self) -> bool:
        return self._instrument is not None and self._info is not None

    @property
    def info(self) -> InstrumentInfo | None:
        return self._info

    @property
    def background(self) -> Spectrum | None:
        return self._background

    @property
    def background_time(self) -> datetime | None:
        return self._background_time

    @property
    def busy(self) -> bool:
        return self._busy

    def set_driver(self, driver_id: str) -> None:
        self.driver_id = driver_id
        self._settings.setValue(_LAST_DRIVER_KEY, driver_id)
        self._restore_driver_settings(driver_id)
        self.settings_changed.emit()

    def set_sim_sample(self, name: str) -> None:
        self.sim_sample = name
        if self._instrument is not None and hasattr(self._instrument, "set_sample"):
            worker = CallWorker(self._locked(self._instrument.set_sample), name)
            worker.failed.connect(lambda msg: self.error.emit("Set Sample", msg))
            start_worker(worker)

    def update_params(self, **kwargs) -> None:
        """Set any of scans/resolution/x_min/x_max/apodization/final_format,
        persist them for the current driver, and tell every view to refresh
        (so e.g. editing scans in the Simple settings dialog updates the
        Advanced dock's spin box too, and vice versa)."""
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save_driver_settings()
        self.settings_changed.emit()

    # -- connect / disconnect ---------------------------------------------------------

    def connect_instrument(self) -> None:
        driver_id = self.driver_id
        if not driver_id:
            self.error.emit("Connect", "No instrument driver available.")
            return
        try:
            if driver_id == "simulated":
                try:
                    instrument = registry.create(driver_id, sample=self.sim_sample, scan_time_s=SIMULATED_SCAN_TIME_S)
                except TypeError:
                    instrument = registry.create(driver_id)
            else:
                instrument = registry.create(driver_id)
        except Exception as exc:  # noqa: BLE001
            self.error.emit("Connect", f"Could not create driver:\n{exc}")
            return

        self._instrument = instrument
        self.status_message.emit("Connecting...")
        worker = CallWorker(self._locked(instrument.connect))
        worker.succeeded.connect(self._on_connected)
        worker.failed.connect(self._on_connect_failed)
        start_worker(worker)

    def _on_connected(self, info: InstrumentInfo) -> None:
        self._info = info
        if info.resolutions:
            self.resolutions = info.resolutions
            if self.resolution not in info.resolutions:
                self.resolution = info.resolutions[0]
        self.connection_changed.emit(True, info)
        self.settings_changed.emit()
        self.status_message.emit(f"Connected to {info.vendor} {info.model}")

    def _on_connect_failed(self, message: str) -> None:
        self._instrument = None
        self.connection_changed.emit(False, None)
        self.error.emit("Connect", message)

    def disconnect_instrument(self) -> None:
        if self._instrument is None:
            return
        worker = CallWorker(self._locked(self._instrument.disconnect))
        worker.succeeded.connect(lambda *_: self._on_disconnected())
        worker.failed.connect(lambda msg: (self._on_disconnected(), self.error.emit("Disconnect", msg)))
        start_worker(worker)

    def _on_disconnected(self) -> None:
        self._instrument = None
        self._info = None
        self.connection_changed.emit(False, None)
        self.status_message.emit("Disconnected")

    def request_diagnostics(self) -> None:
        if self._instrument is None:
            return
        worker = CallWorker(self._locked(self._instrument.diagnostics))
        worker.succeeded.connect(self.diagnostics_ready.emit)
        worker.failed.connect(lambda msg: self.error.emit("Diagnostics", msg))
        start_worker(worker)

    # -- per-driver settings memory ---------------------------------------------------

    def _driver_settings_prefix(self, driver_id: str) -> str:
        return f"collect/{driver_id}"

    def save_driver_settings(self) -> None:
        if not self.driver_id:
            return
        prefix = self._driver_settings_prefix(self.driver_id)
        s = self._settings
        s.setValue(f"{prefix}/scans", self.scans)
        s.setValue(f"{prefix}/resolution", self.resolution)
        s.setValue(f"{prefix}/x_min", self.x_min)
        s.setValue(f"{prefix}/x_max", self.x_max)
        s.setValue(f"{prefix}/apodization", self.apodization)
        s.setValue(f"{prefix}/format_v2", self.final_format)

    def _restore_driver_settings(self, driver_id: str) -> None:
        prefix = self._driver_settings_prefix(driver_id)
        s = self._settings

        self.scans = s.value(f"{prefix}/scans", DEFAULT_SCANS, type=int)

        resolution = s.value(f"{prefix}/resolution", None)
        if resolution is not None:
            self.resolution = float(resolution)

        x_min = s.value(f"{prefix}/x_min", None)
        if x_min is not None:
            self.x_min = float(x_min)

        x_max = s.value(f"{prefix}/x_max", None)
        if x_max is not None:
            self.x_max = float(x_max)

        apodization = s.value(f"{prefix}/apodization", "")
        if apodization:
            self.apodization = apodization

        # "format_v2", not "format": see the comment on FORMATS above -- this
        # intentionally ignores any pre-this-change saved value, so a fresh
        # install and an upgraded old install both land on the new
        # %Transmittance default until the user actually picks a format.
        fmt = s.value(f"{prefix}/format_v2", "")
        if fmt:
            self.final_format = fmt

    # -- collection ---------------------------------------------------------------------

    def build_params(self) -> CollectParams:
        return CollectParams(
            scans=self.scans,
            resolution=self.resolution,
            x_min=self.x_min,
            x_max=self.x_max,
            apodization=self.apodization,
        )

    def start_collect(self, mode: str) -> None:
        if self._instrument is None:
            return
        self.save_driver_settings()
        self._pending_mode = mode
        params = self.build_params()
        # Tell the driver what this collection is for; drivers may use it
        # (the simulator collects an empty beam for a background).
        params.extra["purpose"] = mode

        worker = CollectWorker(self._instrument, params, lock=self._instrument_lock)
        self._worker = worker
        worker.progress.connect(lambda done, total: self.progress.emit(done, total))
        worker.succeeded.connect(self._on_collect_succeeded)
        worker.cancelled.connect(self._on_collect_cancelled)
        worker.failed.connect(self._on_collect_failed)
        self._busy = True
        self.collect_started.emit(mode)
        start_worker(worker)

    def cancel(self) -> None:
        if isinstance(self._worker, CollectWorker):
            self._worker.cancel()

    def _on_collect_succeeded(self, spectrum: Spectrum) -> None:
        self._busy = False
        mode = self._pending_mode
        if spectrum.acquired is None:
            # Drivers don't timestamp their data; stamp it here so the saved
            # file and the PDF report's Date show when it was collected.
            spectrum = spectrum.copy(acquired=datetime.now())
        label = "Background (single beam)" if mode == "background" else "Sample (single beam)"
        self.preview_ready.emit(spectrum, label)

        if mode == "background":
            self._background = spectrum
            self._background_time = datetime.now()
            self.background_changed.emit()
            name = f"Background {self._background_time:%H:%M:%S}"
            self.collect_finished.emit("background", spectrum, name)
            self.status_message.emit("Background collected. Place your sample, then click Collect sample.")
            return

        if self._background is None:
            self.error.emit("Collect Sample", "Collect a background first.")
            return

        to_unit = FORMATS[self.final_format]
        try:
            result = ratio(spectrum, self._background, to=to_unit)
        except Exception as exc:  # noqa: BLE001
            self.error.emit("Collect Sample", str(exc))
            return
        self.collect_finished.emit("sample", result, "Sample")
        self.status_message.emit("Collected and ratioed")

    def _on_collect_cancelled(self) -> None:
        self._busy = False
        self.collect_cancelled.emit(self._pending_mode or "")
        self.status_message.emit("Collection cancelled")

    def _on_collect_failed(self, message: str) -> None:
        self._busy = False
        self.collect_failed.emit(self._pending_mode or "", message)
        self.error.emit("Collect", message)

    # -- auto-named samples (Simple mode) ------------------------------------------------

    def next_sample_name(self) -> str:
        """'Sample N', incrementing for as long as this controller (i.e. this
        app session) lives -- never reset back to 1 until the app restarts."""
        self._sample_counter += 1
        return f"Sample {self._sample_counter}"

    def skip_sample_names(self, names) -> None:
        """Carry on numbering after any 'Sample N' in ``names`` (a reopened
        session), so new samples don't reuse a number."""
        for name in names:
            parts = str(name).split()
            if len(parts) == 2 and parts[0] == "Sample" and parts[1].isdigit():
                self._sample_counter = max(self._sample_counter, int(parts[1]))

    # -- background age -------------------------------------------------------------------

    def background_age_minutes(self) -> int | None:
        if self._background_time is None:
            return None
        return int((datetime.now() - self._background_time).total_seconds() // 60)

    def background_is_stale(self) -> bool:
        if self._background_time is None:
            return False
        return (datetime.now() - self._background_time) > BACKGROUND_WARN_AFTER

    # -- background save/load ------------------------------------------------------------

    def save_background(self, path: str) -> None:
        if self._background is None:
            return
        import openftir.io as io_mod

        io_mod.save([self._background], path)
        self.status_message.emit(f"Background saved to {path}")

    def load_background(self, path: str) -> None:
        import openftir.io as io_mod

        spectra = io_mod.load(path)
        if not spectra:
            self.error.emit("Load Background", "That file contains no spectra.")
            return
        spectrum = spectra[0]
        self._background = spectrum
        self._background_time = local_naive(spectrum.acquired) or datetime.now()
        self.background_changed.emit()
        name = f"Background {self._background_time:%H:%M:%S}"
        self.collect_finished.emit("background", spectrum, name)
        self.status_message.emit(f"Background loaded from {path}")

    def restore_background(self, spectrum: Spectrum, collected: datetime | None) -> None:
        """Use ``spectrum`` as the background again (reopening a session):
        no new list entry, and its age counts from when it was collected."""
        self._background = spectrum
        self._background_time = local_naive(collected) or local_naive(spectrum.acquired) or datetime.now()
        self.background_changed.emit()

    def clear_background(self) -> None:
        if self._background is None:
            return
        self._background = None
        self._background_time = None
        self.background_changed.emit()

    # -- helpers ----------------------------------------------------------------------------

    def _locked(self, fn):
        """Wrap an instrument call so only one runs at a time (drivers are
        not required to be thread-safe)."""

        def call(*args, **kwargs):
            with self._instrument_lock:
                return fn(*args, **kwargs)

        return call
