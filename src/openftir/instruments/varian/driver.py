"""The Varian 3100 FT-IR (Excalibur series) driver.

Sends the same requests, in the same order, that Varian Resolutions Pro
sent in the captures (``docs/varian-protocol.md``), with the lab's
settings: 4 cm-1, KBr beamsplitter, 5 kHz, Sensitivity 4, gain ranging
radius 40. Nothing is sent that wasn't seen in a capture.

The bench needs its firmware loaded on every connect (and Resolutions
reloads it before every collection, so this driver does too). The
firmware is Varian's; the user supplies the file.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time

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
from openftir.processing.fft import asymmetric_mertz_single_beam
from openftir.spectrum import Spectrum, XUnit, YUnit

from . import protocol as p
from .calibration import Calibrator, Point, Reading
from .transport import UsbTransport, VarianTransport

log = logging.getLogger(__name__)

__all__ = ["VarianInstrument", "scans_to_single_beam"]

#: Bench requests (REQ_BENCH wIndex) Resolutions sends before every
#: collection, between the firmware reload and the alignment read. Several
#: carry the acquisition settings; their meaning isn't decoded, so they are
#: only valid for the settings captured (see module docstring).
_SETUP_READS = (0x0135, 0xB500)
_SETUP_READS_2 = (0xB6FF, 0xB801)
#: Bench register 0x10 bit set once the cold-start calibration has run.
_READY = 0x08
#: Signal reads during calibration: 1056 samples.
_CAL_POINTS = 1056
_PRE_ALIGN_READS = (0xB703, 0x030E, 0x030C)
_BUS_PROBES = (0x44, 0x46, 0x4A, 0x48, 0x42)
#: Scan geometry after the A2/A3 offset: gain zone start 475, gain zone
#: length 80, 3517 points after it, then 0xBA00.
_SCAN_GEOMETRY = (0xA5DB, 0xA401, 0xA750, 0xA600, 0xA9BD, 0xA80D, 0xBA00)
#: Accessory ID channels in the order Resolutions reads them at connect.
_ID_CHANNELS = (4, 5, 6, 2, 3, 4)

SUPPORTED_RESOLUTIONS = (4.0,)

_SCAN_TIMEOUT_S = 10.0     # one scan takes ~1.5 s
_ALIGN_TIMEOUT_S = 5.0
_READER_JOIN_S = 15.0
_MIN_BURST_COUNTS = 200    # alignment centre burst below this = no IR signal


def scans_to_single_beam(scans: list[np.ndarray], params: CollectParams, laser_wavenumber: float) -> tuple[np.ndarray, Spectrum]:
    """Co-add gain-corrected scans (counts) and FFT them like Resolutions."""
    volts = p.to_volts(np.mean(scans, axis=0))
    sb = asymmetric_mertz_single_beam(
        volts,
        p.ZPD_INDEX,
        laser_wavenumber,
        apodization=params.apodization,
        zero_fill=params.zero_fill,
        x_min=params.x_min,
        x_max=params.x_max,
    )
    return volts, sb


class _BulkReader(threading.Thread):
    """Reads groups of bulk transfers in the background.

    Resolutions queues each bulk read before triggering the bench, and keeps
    reading while it sends control requests (e.g. stop after the next scan),
    so this runs alongside the driver's control requests. Each group is one
    scan (or one alignment read); ``stop()`` takes effect between groups so a
    scan is never left half-read.
    """

    def __init__(self, transport: VarianTransport, groups: list[tuple[int, ...]], timeout: float) -> None:
        super().__init__(name="varian-bulk", daemon=True)
        self._transport = transport
        self._groups = groups
        self._timeout = timeout
        self._stop_flag = threading.Event()
        self.results: queue.Queue = queue.Queue()

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        try:
            for sizes in self._groups:
                if self._stop_flag.is_set():
                    break
                parts = []
                for size in sizes:
                    data = self._transport.bulk_read(size, self._timeout)
                    if not data:
                        raise InstrumentError(f"Varian: no data from the bench for {self._timeout:g} s")
                    if len(data) != size:
                        raise InstrumentError(f"Varian: expected a {size}-byte transfer, got {len(data)} bytes")
                    parts.append(data)
                self.results.put(parts)
        except Exception as exc:  # noqa: BLE001 - handed to the driver thread
            self.results.put(exc)
        finally:
            self.results.put(None)


class VarianInstrument(Instrument):
    """Varian 3100 FT-IR (Excalibur series), driven directly over USB."""

    driver_id = "varian"
    display_name = "Varian 3100 FT-IR (USB)"
    default_apodization = "norton-beer-medium"
    required_files = {
        "firmware": ("firmware file (.hex)", "Varian firmware (*.hex);;All files (*)"),
    }

    #: seconds to let the actuators / gains settle before a calibration read
    move_settle_s = 0.4
    gain_settle_s = 0.1

    def __init__(
        self,
        transport: VarianTransport | None = None,
        firmware: str | None = None,
        laser_wavenumber: float | str = p.DEFAULT_LASER_WAVENUMBER,
    ) -> None:
        self._firmware = p.load_firmware(firmware)
        self._transport: VarianTransport = transport if transport is not None else UsbTransport()
        self._laser = float(laser_wavenumber)
        self._connected = False
        self._ids: dict[int, str] = {}
        self._gains: dict[int, int] = {}
        self.calibration = None
        #: optional callback for progress text (the app shows it in the status bar)
        self.on_status = None

    # -- Instrument interface ---------------------------------------------------

    def connect(self) -> InstrumentInfo:
        self._transport.open()
        try:
            bench = self._handshake()
            self._prepare(None)
        except Exception:
            self._transport.close()
            raise
        self._connected = True
        digest = hashlib.sha256(self._firmware).hexdigest()
        return InstrumentInfo(
            vendor="Varian",
            model="3100 FT-IR (Excalibur)",
            firmware=f"user file, sha256 {digest[:12]}",
            laser_wavenumber=self._laser,
            resolutions=SUPPORTED_RESOLUTIONS,
            extra={"bench": bench, "accessory_ids": dict(self._ids)},
        )

    def _handshake(self) -> tuple[int, int]:
        """Resolutions' connect sequence: firmware, accessory IDs, status."""
        self._status()
        self._upload_firmware()
        self._read_id(7)
        bench = (self._bench(0x0801)[0], self._bench(0x0802)[0])
        running = self._transport.control_in(p.REQ_STATUS, 0, 2, 2)
        if running != p.STATUS_FIRMWARE_RUNNING:
            raise InstrumentError(
                f"Varian: the bench did not start its firmware (status {running.hex(' ')}). "
                "Check the firmware file, then switch the bench off and on."
            )
        self._transport.control_in(p.REQ_STATUS, 0, 1, 2)
        for channel in _ID_CHANNELS:
            self._read_id(channel)
        return bench

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self._transport.control_out(p.REQ_SET, 0, p.REG_CONTINUOUS)
        except InstrumentError as exc:
            log.warning("Varian: error stopping the bench on disconnect: %s", exc)
        finally:
            self._transport.close()
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def collect_single_beam(
        self,
        params: CollectParams,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Spectrum:
        scans, meta = self._collect(params, progress, cancel)
        _volts, sb = scans_to_single_beam(scans, params, self._laser)
        return sb.copy(title="Varian 3100 single beam", meta={**sb.meta, **meta})

    def collect_interferogram(self, params: CollectParams) -> Spectrum:
        scans, meta = self._collect(params, None, None)
        volts = p.to_volts(np.mean(scans, axis=0))
        return Spectrum(
            x=np.arange(volts.size, dtype=float) - p.ZPD_INDEX,
            y=volts,
            x_unit=XUnit.POINTS,
            y_unit=YUnit.INTERFEROGRAM,
            title="Varian 3100 interferogram",
            meta={**meta, "zpd_index": p.ZPD_INDEX},
            history=["varian co-added interferogram (volts)"],
        )

    def diagnostics(self) -> DiagnosticsReport:
        if not self._connected:
            return DiagnosticsReport(ok=False, items={"connection": "disconnected"})
        try:
            running = self._transport.control_in(p.REQ_STATUS, 0, 2, 2)
        except InstrumentError as exc:
            return DiagnosticsReport(ok=False, items={"connection": f"error: {exc}"})
        ok = running == p.STATUS_FIRMWARE_RUNNING
        items = {"firmware": "running" if ok else f"not running (status {running.hex(' ')})"}
        for channel, rom in sorted(self._ids.items()):
            items[f"accessory {channel}"] = "present" if rom else "none"
        return DiagnosticsReport(ok=ok, items=items)

    # -- requests ---------------------------------------------------------------

    def _status(self) -> None:
        self._transport.control_in(p.REQ_STATUS, 0, 1, 2)
        self._transport.control_in(p.REQ_STATUS, 0, 2, 2)

    def _upload_firmware(self) -> None:
        for chunk in p.firmware_chunks(self._firmware):
            self._transport.control_out(p.REQ_FIRMWARE, len(chunk), 0, chunk)

    def _bench(self, index: int) -> bytes:
        return self._transport.control_in(p.REQ_BENCH, 0, index, 2)

    def _set(self, register: int, value: int) -> None:
        self._transport.control_out(p.REQ_SET, value & 0xFFFF, register)

    def _read_id(self, channel: int) -> None:
        present = self._transport.control_in(p.REQ_ID, channel, 0, 2) == b"\x00\x00"
        rom = self._transport.control_in(p.REQ_ID, 0, 1, 10)
        if present:
            self._transport.control_in(p.REQ_ID, 0, 2, 2)
            self._transport.control_in(p.REQ_ID, 0, 4, 34)
            self._ids[channel] = rom[2:].hex()
        else:
            self._ids.setdefault(channel, "")

    def _read_register(self, register: int) -> int:
        return self._bench(0x0200 | register)[0]

    def _write_register(self, register: int, value: int) -> None:
        self._bench(((0x40 | register) << 8) | (value & 0xFF))

    def _rewrite_register(self, register: int, change=lambda v: v) -> None:
        # Resolutions reads these bench registers and writes them back,
        # sometimes with a bit changed.
        self._write_register(register, change(self._read_register(register)))

    def _bus_read(self, address: int) -> int:
        self._transport.control_in(p.REQ_BUS, address, 0, 2)
        value = self._transport.control_in(p.REQ_BUS, 1, 3, 2)[0]
        self._transport.control_in(p.REQ_BUS, 0, 1, 2)
        return value

    def _bus_write(self, address: int, value: int) -> None:
        self._transport.control_in(p.REQ_BUS, address, 0, 2)
        self._transport.control_in(p.REQ_BUS, value, 2, 2)
        self._transport.control_in(p.REQ_BUS, 0, 1, 2)

    def _set_layout(self, nbytes: int) -> None:
        chunks, size, last = p.transfer_layout(nbytes)
        self._set(p.REG_CHUNKS, chunks)
        self._set(p.REG_CHUNK_SIZE, size)
        self._set(p.REG_LAST_CHUNK, last)
        self._set(p.REG_SAMPLE_BYTES, 2)

    def _prepare(self, cancel: threading.Event | None) -> None:
        """Firmware reload and bench setup, as Resolutions does before each
        collection, with the cold-start calibration first if the bench has
        been switched on since it last ran."""
        self._status()
        self._upload_firmware()
        self._set(p.REG_CONTINUOUS, 0)
        self._set(p.REG_CONTINUOUS, 0)
        self._bench(0x030D)
        self._bench(0x0300)
        if not self._read_register(0x10) & _READY:
            self._set(p.REG_CONTINUOUS, 0)
            self._bench(0x030D)
            self._setup(cold=True)
            self._calibrate(cancel)
            self._bench(0x0300)
            self._read_register(0x10)
        self._setup(cold=False)

    def _setup(self, cold: bool) -> None:
        for index in _SETUP_READS:
            self._bench(index)
        self._bench(0xAB03 if cold else 0xAB04)
        self._rewrite_register(0x0E)
        self._rewrite_register(0x01)
        for index in _SETUP_READS_2:
            self._bench(index)
        # Bit 0 of register 0x0E is cleared on a cold bench and set on a
        # calibrated one; bit 7 is always set.
        self._rewrite_register(0x0E, (lambda v: v & ~0x01) if cold else (lambda v: v | 0x01))
        self._rewrite_register(0x0E, lambda v: v | 0x80)
        for _ in range(2):
            # Same value back with the top three bits cleared, as Resolutions does.
            self._bus_write(0x40, self._bus_read(0x41) & 0x1F)
        # (the first probe is skipped on a cold bench, in both captures)
        for address in _BUS_PROBES[1:] if cold else _BUS_PROBES:
            self._transport.control_in(p.REQ_BUS, address, 0, 2)
        for index in _PRE_ALIGN_READS:
            self._bench(index)

    # -- cold-start calibration ------------------------------------------------------

    def _say(self, message: str) -> None:
        log.info("Varian: %s", message)
        if self.on_status is not None:
            self.on_status(message)

    def _calibrate(self, cancel: threading.Event | None) -> None:
        self._say("The bench was just switched on: aligning it (about 3 minutes)")
        self._set_layout(2 * _CAL_POINTS)
        self._bench(0xA000 | (_CAL_POINTS >> 8))
        self._bench(0xA100 | (_CAL_POINTS & 0xFF))
        self._read_register(0x10)
        self._write_register(0x10, 0x00)
        saved_0e = self._read_register(0x0E)
        saved_01 = self._read_register(0x01)
        self._gains = {}

        def check() -> None:
            if cancel is not None and cancel.is_set():
                raise CollectionCancelled("Varian alignment cancelled")

        calibrator = Calibrator(
            move=self._cal_move,
            set_gains=self._cal_gains,
            select=self._cal_select,
            read=self._cal_read,
            check=check,
            status=self._say,
        )
        result = calibrator.run()
        self._read_register(0x10)
        self._write_register(0x10, _READY)
        self._write_register(0x0E, saved_0e)
        self._write_register(0x01, saved_01)
        self.calibration = result
        self._say(f"Bench aligned ({result.reads} reads)")
        for line in result.log:
            log.info("Varian calibration: %s", line)

    def _cal_move(self, point: Point) -> None:
        b0, b1, af = point
        self._bench(0xB000 | b0)
        self._bench(0xB100 | b1)
        self._bench(0xAF00 | af)
        self._bench(0x0308)
        if self.move_settle_s:
            time.sleep(self.move_settle_s)

    def _cal_gains(self, gains: dict[int, int], force: bool = False) -> None:
        changed = [reg for reg in (0xBD, 0xBE, 0xBF) if force or self._gains.get(reg) != gains[reg]]
        for reg in changed:
            self._bench((reg << 8) | gains[reg])
        self._bench(0x0301)
        self._gains.update(gains)
        if self.gain_settle_s:
            time.sleep(self.gain_settle_s)

    def _cal_select(self, channel: int) -> None:
        self._write_register(0x0E, 0x00)
        self._write_register(0x01, channel)

    def _cal_read(self) -> Reading:
        reader = self._trigger([(2 * _CAL_POINTS, 2)], _ALIGN_TIMEOUT_S, 0x0305, None)
        result = self._next_result(reader, None)
        reader.join(_READER_JOIN_S)
        if result is None:
            return Reading(0.0, 0.0, ok=False)
        samples = p.decode_samples(result[0])
        return Reading(float(samples.mean()), float(samples.min()), ok=result[1] == p.TERMINATOR)

    def _trigger(self, groups: list[tuple[int, ...]], timeout: float, status: int, continuous: int | None) -> _BulkReader:
        reader = _BulkReader(self._transport, groups, timeout)
        reader.start()
        if continuous is not None:
            self._set(p.REG_CONTINUOUS, continuous)
        self._set(p.REG_ARM, 0xFFFF)
        self._set(p.REG_TRIGGER, 0xFFFF)
        self._transport.control_in(p.REQ_ARM_STATUS, 0, status, 2)
        return reader

    def _alignment_read(self) -> np.ndarray:
        self._set_layout(2 * p.ALIGN_POINTS)
        self._bench(0xA000 | (p.ALIGN_POINTS >> 8))
        self._bench(0xA100 | (p.ALIGN_POINTS & 0xFF))
        reader = self._trigger([(2 * p.ALIGN_POINTS, 2)], _ALIGN_TIMEOUT_S, 0x0303, None)
        result = self._next_result(reader, None)
        reader.join(_READER_JOIN_S)
        if result is None:
            raise InstrumentError("Varian: the alignment read returned no data")
        self._check_terminator(result[1])
        return p.decode_samples(result[0])

    @staticmethod
    def _check_terminator(data: bytes) -> None:
        if data != p.TERMINATOR:
            log.warning("Varian: unexpected end-of-scan marker %s", data.hex(" "))

    @staticmethod
    def _next_result(reader: _BulkReader, cancel: threading.Event | None):
        while True:
            if cancel is not None and cancel.is_set():
                raise CollectionCancelled("Varian collection cancelled")
            try:
                item = reader.results.get(timeout=0.2)
            except queue.Empty:
                continue
            if isinstance(item, Exception):
                raise item
            return item

    # -- collection ---------------------------------------------------------------

    def _collect(
        self,
        params: CollectParams,
        progress: ProgressCallback | None,
        cancel: threading.Event | None,
    ) -> tuple[list[np.ndarray], dict]:
        if not self._connected:
            raise InstrumentError("Varian is not connected")
        if params.scans < 1:
            raise ValueError("params.scans must be >= 1")
        if params.resolution not in SUPPORTED_RESOLUTIONS:
            raise InstrumentError(
                f"Varian: only {SUPPORTED_RESOLUTIONS[0]:g} cm-1 is supported so far "
                f"(asked for {params.resolution:g} cm-1)."
            )
        if cancel is not None and cancel.is_set():
            raise CollectionCancelled("Varian collection cancelled")

        self._prepare(cancel)
        align = self._alignment_read()
        burst = float(np.max(np.abs(align - np.median(align))))
        if burst < _MIN_BURST_COUNTS:
            raise InstrumentError(
                "Varian: no interferogram signal in the alignment read. Check the "
                "source is on and nothing is blocking the beam."
            )
        offset = p.scan_offset(align)

        self._set_layout(sum(p.SCAN_TRANSFERS))
        self._bench(0xA300 | (offset & 0xFF))
        self._bench(0xA200 | ((offset >> 8) & 0xFF))
        for index in _SCAN_GEOMETRY:
            self._bench(index)

        n = params.scans
        continuous = n > 1
        reader = self._trigger([(*p.SCAN_TRANSFERS, 2)] * n, _SCAN_TIMEOUT_S, 0x0304, 1 if continuous else 0)
        scans: list[np.ndarray] = []
        try:
            while len(scans) < n:
                result = self._next_result(reader, cancel)
                if result is None:
                    raise InstrumentError("Varian: the bench stopped sending scans")
                first, second, end = result
                self._check_terminator(end)
                scans.append(p.correct_scan(first + second))
                if continuous and len(scans) == n - 1:
                    # Stop after the scan now in progress (Resolutions does
                    # this as the second-to-last scan finishes).
                    self._set(p.REG_CONTINUOUS, 0)
                    continuous = False
                if progress is not None:
                    progress(len(scans), n)
        except BaseException:
            reader.stop()
            try:
                self._set(p.REG_CONTINUOUS, 0)
            except InstrumentError as exc:
                log.warning("Varian: error stopping the bench: %s", exc)
            reader.join(_READER_JOIN_S)
            raise
        reader.join(_READER_JOIN_S)
        self._set(p.REG_CONTINUOUS, 0)
        self._bench(0x030D)

        zpd = p.centre_burst(np.mean(scans, axis=0))
        if abs(zpd - p.ZPD_INDEX) > 2:
            log.warning("Varian: centre burst at sample %d, expected %d", zpd, p.ZPD_INDEX)
        log.info("Varian: %d scan(s), alignment burst %.0f counts, scan offset %d, centre burst at %d",
                 n, burst, offset, zpd)
        meta = {
            "driver": self.driver_id,
            "instrument": "Varian 3100 FT-IR",
            "scans": n,
            "resolution": params.resolution,
            "laser_wavenumber": self._laser,
            "scan_offset": offset,
            "zpd_found": zpd,
        }
        return scans, meta
