"""The real Nicolet Summit X driver.

Protocol and command sequence come from ``docs/summit-protocol.md``
("Connection sequence used by Paradigm" and "Collecting scans"). Only
commands/values documented there are ever sent (except ``peakPos``/``sdp``,
which depend on resolution -- see :mod:`.resolution`); motor, ``valWheelPos``,
``i2cMsg`` and mapping commands are never sent by this driver.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Sequence

import numpy as np

from openftir.instruments.base import (
    CollectionCancelled,
    CollectParams,
    DiagnosticsReport,
    Instrument,
    InstrumentError,
    InstrumentInfo,
    ProgressCallback,
)
from openftir.processing.fft import interferogram_to_single_beam
from openftir.spectrum import Spectrum, XUnit, YUnit

from .protocol import build_get, build_set, parse_reply
from .resolution import SUPPORTED_RESOLUTIONS, UNVERIFIED_RESOLUTIONS, scan_geometry
from .stream import ScanAssembler
from .transport import SummitTransport, UsbTransport

log = logging.getLogger(__name__)

__all__ = ["SummitInstrument", "coadd_interferogram", "coadd_to_single_beam"]

#: One combined ``get`` at connect time (docs/summit-protocol.md step 4,
#: plus benchModel/firmVer/laserFreq/serialNum from step 1/2).
_CONNECT_KEYS = (
    "benchModel",
    "firmVer",
    "laserFreq",
    "serialNum",
    "maxRes",
    "apertureCfg",
    "bpTable",
    "detGainCfg",
    "hpfCfg",
    "lpfCfg",
    "sdpCfg",
    "sourceCfg",
)

#: Live diagnostics (docs/summit-protocol.md "Diagnostics keys"), minus the
#: motor/alignment-only values (laserGain*/endplate*) this driver never
#: touches.
_DIAG_KEYS = (
    "plus33v",
    "plus5v",
    "plus12v",
    "minus12v",
    "laserX",
    "laserY",
    "laserR",
    "irPower",
    "irCurrent",
    "laserCurrent",
    "boardTemp",
    "modulTemp",
    "laserTemp",
    "humidity",
    "driveVolt",
)
_DIAG_CFG_KEYS = tuple(f"{key}Cfg" for key in _DIAG_KEYS)

_MOTORS_DONE_TIMEOUT_S = 30.0
_MOTORS_DONE_POLL_INTERVAL_S = 0.4
_DATA_READ_TIMEOUT_S = 10.0   # give up after this long without scan data
_DATA_POLL_S = 0.5            # single USB read timeout, so cancel stays responsive
_DRAIN_TIMEOUT_S = 0.05
_DRAIN_MAX_READS = 200


def _collect_state(state: int) -> dict:
    # Paradigm always sends the full state set when starting/stopping.
    return {"stepScanState": 0, "collectState": state, "servoState": 1, "extTrigger": 0}
_DATA_CHUNK_SIZE = 4096


def coadd_interferogram(scans: Sequence[np.ndarray], peak_pos: int) -> np.ndarray:
    """Average raw EP 0x83 scans, trimmed symmetrically around ``peak_pos``.

    Each scan is cut to its first ``2*peak_pos`` samples (the centre burst
    sits at index ``peak_pos``; the doc calls the last few dozen samples of
    a scan unreliable, so they're never used) before averaging.
    """
    n = 2 * peak_pos
    trimmed = [np.asarray(s, dtype=float)[:n] for s in scans]
    return np.mean(trimmed, axis=0)


def coadd_to_single_beam(
    scans: Sequence[np.ndarray],
    peak_pos: int,
    laser_freq: float,
    params: CollectParams,
) -> tuple[np.ndarray, Spectrum]:
    """Co-add raw scans and FFT them to a single-beam spectrum.

    Pulled out of :class:`SummitInstrument` so it can be exercised without
    any hardware/transport at all -- used by the (optional, data-gated)
    validation test that replays real captured scans.
    """
    igram = coadd_interferogram(scans, peak_pos)
    sb = interferogram_to_single_beam(
        igram,
        laser_wavenumber=2.0 * laser_freq,
        sampling_interval=1,
        apodization=params.apodization,
        zero_fill=params.zero_fill,
        phase_correction=params.phase_correction,
        x_min=params.x_min,
        x_max=params.x_max,
    )
    return igram, sb


def _range_from(value: object) -> tuple[float, float] | None:
    if isinstance(value, dict) and "min" in value and "max" in value:
        try:
            return float(value["min"]), float(value["max"])
        except (TypeError, ValueError):
            return None
    return None


class SummitInstrument(Instrument):
    """Thermo Nicolet Summit X, driven directly over USB."""

    driver_id = "summit"
    display_name = "Thermo Nicolet Summit (USB)"

    def __init__(self, transport: SummitTransport | None = None) -> None:
        self._transport: SummitTransport = transport if transport is not None else UsbTransport()
        self._connected = False
        self._laser_freq = 11685.125977
        self._serial = ""
        self._firmware = ""
        self._bench_model = ""
        self._sdp_range: tuple[float, float] | None = None
        self._diag_ranges: dict[str, tuple[float, float]] = {}

    # -- Instrument interface -------------------------------------------------

    def connect(self) -> InstrumentInfo:
        self._transport.open()
        try:
            reply = parse_reply(self._transport.command(build_get(_CONNECT_KEYS)))

            self._bench_model = str(reply.get("benchModel", "?"))
            self._firmware = str(reply.get("firmVer", "") or "")
            self._laser_freq = reply.get_float("laserFreq", self._laser_freq) or self._laser_freq
            self._serial = str(reply.get("serialNum", "") or "")
            max_res = reply.get_float("maxRes")
            self._sdp_range = _range_from(reply.values.get("sdpCfg"))

            diag_reply = parse_reply(self._transport.command(build_get(_DIAG_CFG_KEYS)))
            self._diag_ranges = {}
            for key in _DIAG_KEYS:
                rng = _range_from(diag_reply.values.get(f"{key}Cfg"))
                if rng is not None:
                    self._diag_ranges[key] = rng
        except Exception:
            self._transport.close()
            raise

        self._connected = True
        return InstrumentInfo(
            vendor="Thermo Fisher Scientific",
            model=f"Nicolet Summit (bench {self._bench_model})",
            serial=self._serial,
            firmware=self._firmware,
            laser_wavenumber=2.0 * self._laser_freq,
            resolutions=SUPPORTED_RESOLUTIONS,
            extra={
                "bench_model": self._bench_model,
                "max_resolution_cm": max_res,
                "unverified_resolutions": UNVERIFIED_RESOLUTIONS,
                "detector_gains": reply.values.get("detGainCfg"),
                "beampath_table": reply.values.get("bpTable"),
            },
        )

    def disconnect(self) -> None:
        if self._connected:
            try:
                self._transport.close()
            finally:
                self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- collection -----------------------------------------------------------

    def collect_single_beam(
        self,
        params: CollectParams,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Spectrum:
        peak_pos, scans, meta = self._collect_scans(params, progress, cancel)
        _igram, sb = coadd_to_single_beam(scans, peak_pos, self._laser_freq, params)
        return sb.copy(title="Nicolet Summit single beam", meta={**sb.meta, **meta})

    def collect_interferogram(self, params: CollectParams) -> Spectrum:
        peak_pos, scans, meta = self._collect_scans(params, progress=None, cancel=None)
        igram = coadd_interferogram(scans, peak_pos)
        return Spectrum(
            x=np.arange(igram.size, dtype=float),
            y=igram,
            x_unit=XUnit.POINTS,
            y_unit=YUnit.INTERFEROGRAM,
            title="Nicolet Summit interferogram",
            meta=meta,
            history=["summit co-added interferogram"],
        )

    def diagnostics(self) -> DiagnosticsReport:
        if not self.connected:
            return DiagnosticsReport(ok=False, items={"connection": "disconnected"})
        try:
            reply = parse_reply(self._transport.command(build_get(_DIAG_KEYS)))
        except InstrumentError as exc:
            return DiagnosticsReport(ok=False, items={"connection": f"error: {exc}"})

        items: dict[str, str] = {}
        ok = True
        for key in _DIAG_KEYS:
            value = reply.get_float(key)
            if value is None:
                items[key] = "unavailable"
                continue
            rng = self._diag_ranges.get(key)
            if rng is not None and not (rng[0] <= value <= rng[1]):
                items[key] = f"{value:g} (OUT OF RANGE {rng[0]:g}..{rng[1]:g})"
                ok = False
            else:
                items[key] = f"{value:g}"
        return DiagnosticsReport(ok=ok, items=items)

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _check_cancel(cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise CollectionCancelled("Summit collection cancelled")

    def _collect_scans(
        self,
        params: CollectParams,
        progress: ProgressCallback | None,
        cancel: threading.Event | None,
    ) -> tuple[int, list[np.ndarray], dict]:
        if not self.connected:
            raise InstrumentError("Summit is not connected")
        if params.scans < 1:
            raise ValueError("params.scans must be >= 1")
        self._check_cancel(cancel)

        peak_pos, sdp = scan_geometry(params.resolution, self._laser_freq, self._sdp_range)
        gain = 1 if params.gain == "auto" else int(params.gain)

        collecting = False
        good_scans: list[np.ndarray] = []
        scan_ids: list[int] = []
        try:
            self._send_scan_setup(peak_pos, sdp, gain)
            self._poll_motors_done(cancel)

            self._transport.command(build_set(_collect_state(0)))
            self._drain_data()
            self._transport.command(build_set({"lightbar": 4}))
            collecting = True
            self._transport.command(build_set(_collect_state(1)))

            assembler = ScanAssembler()
            min_len = 2 * peak_pos
            last_data = time.monotonic()
            while len(good_scans) < params.scans:
                self._check_cancel(cancel)
                chunk = self._transport.read_data(_DATA_CHUNK_SIZE, timeout=_DATA_POLL_S)
                if not chunk:
                    if time.monotonic() - last_data > _DATA_READ_TIMEOUT_S:
                        raise InstrumentError(
                            f"Summit: no scan data for {_DATA_READ_TIMEOUT_S:g} s after starting collection"
                        )
                    continue
                last_data = time.monotonic()
                for scan_id, samples, _end_record in assembler.feed(chunk):
                    if samples.size < min_len:
                        continue  # too short -- drop per docs/summit-protocol.md
                    good_scans.append(samples)
                    scan_ids.append(int(scan_id))
                    if progress is not None:
                        progress(len(good_scans), params.scans)
                    if len(good_scans) >= params.scans:
                        break
        finally:
            if collecting:
                self._stop_collection()

        meta = {
            "driver": self.driver_id,
            "scans": params.scans,
            "resolution": params.resolution,
            "peakPos": peak_pos,
            "sdp": sdp,
            "laserFreq": self._laser_freq,
            "serial": self._serial,
            "firmware": self._firmware,
            "scan_ids": scan_ids,
            "gain": params.gain,
        }
        return peak_pos, good_scans, meta

    def _send_scan_setup(self, peak_pos: int, sdp: int, gain: int) -> None:
        # Mirrors Paradigm's setup exactly (docs/summit-protocol.md
        # "Connection sequence", step 5): seven separate `set`s, in this order.
        self._transport.command(build_set({"sampleshuttle": 1}))
        self._transport.command(
            build_set({"ssp": 1, "fwdVel": 0.4747, "peakPos": peak_pos, "sdp": sdp, "detGain": gain})
        )
        self._transport.command(build_set({"hpf": 20}))
        self._transport.command(build_set({"lpf": 11000}))
        self._transport.command(build_set({"rtrVel": 4}))
        self._transport.command(build_set({"bsSlot": 0, "beampathId": 1, "sourceId": 2, "aperture": 100}))
        self._transport.command(build_set({"settleTime": 200, "bidirScan": 0}))

    def _drain_data(self) -> None:
        """Discard leftover EP 0x83 bytes, e.g. the tail of a scan that was
        cut short when a previous collection stopped."""
        for _ in range(_DRAIN_MAX_READS):
            if not self._transport.read_data(_DATA_CHUNK_SIZE, timeout=_DRAIN_TIMEOUT_S):
                return

    def _poll_motors_done(self, cancel: threading.Event | None) -> None:
        deadline = time.monotonic() + _MOTORS_DONE_TIMEOUT_S
        while True:
            self._check_cancel(cancel)
            reply = parse_reply(self._transport.command(build_get(("motorsDone",))))
            if reply.get_int("motorsDone", 0) == 1:
                return
            if time.monotonic() >= deadline:
                raise InstrumentError("Summit: timed out waiting for motorsDone")
            time.sleep(_MOTORS_DONE_POLL_INTERVAL_S)

    def _stop_collection(self) -> None:
        # Always stop collection on the way out (cancel, error, or normal
        # completion) -- never leave the bench streaming/mid-scan.
        for xml in (build_set(_collect_state(0)), build_set({"lightbar": 1})):
            try:
                self._transport.command(xml)
            except InstrumentError as exc:
                log.warning("Summit: error while stopping collection: %s", exc)
