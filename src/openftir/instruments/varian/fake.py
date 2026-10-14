"""In-memory fake of the Varian 3100 bench, for tests and GUI development.

Answers the vendor requests the driver sends with the values seen on the
lab's bench, keeps a log of every request (``self.requests``), and streams
synthetic interferograms on the bulk endpoint in the real format: 4096 +
4048 bytes of big-endian int16 (gain ranging applied around ZPD) and a
2-byte ``00 04`` marker per scan. The centre burst lands where the scan
offset the driver programs (registers A2:A3) puts it, so the offset logic is
exercised too. Pass any Intel HEX file as the firmware; the fake only checks
that the whole file arrived.

With ``cold=True`` the bench starts uninitialised (register 0x10 = 0), so
the driver runs its cold-start calibration: the fake then models three
signal channels whose level depends on the actuator settings (best at
``optimum``) and the channel gains, clipped like the real ADC.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import numpy as np

from . import protocol as p

__all__ = ["FakeVarianTransport", "synthetic_firmware"]

_HALF = p.SCAN_POINTS - p.ZPD_INDEX    # samples after ZPD (incl. ZPD)


def synthetic_firmware(records: int = 40) -> bytes:
    """A made-up but well-formed Intel HEX file (no real firmware)."""
    lines = []
    for n in range(records):
        payload = bytes((n * 16 + k) & 0xFF for k in range(16))
        raw = bytes([16, (n * 16) >> 8 & 0xFF, (n * 16) & 0xFF, 0]) + payload
        lines.append(":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


class FakeVarianTransport:
    def __init__(
        self,
        align_burst: int = 419,
        transmittance: Callable[[np.ndarray], np.ndarray] | None = None,
        noise: float = 0.0,
        scan_time_s: float = 0.0,
        seed: int = 0,
        cold: bool = False,
        optimum: tuple[int, int, int] = (0xB8, 0x7A, 0x66),
    ) -> None:
        self.optimum = optimum
        self.cal_reads = 0
        self.align_burst = align_burst
        self.transmittance = transmittance
        self.noise = noise
        self.scan_time_s = scan_time_s
        self.requests: list[tuple[str, int, int, int]] = []
        self.firmware_uploads = 0
        self.scans_sent = 0
        self.is_open = False
        self._rng = np.random.default_rng(seed)
        self._lock = threading.Lock()
        self._fw = bytearray()
        self._running = False
        self._regs: dict[int, int] = {}
        self._bench_regs = {0x0E: 0x88 if cold else 0x89, 0x01: 0x07, 0x10: 0x00 if cold else 0x08}
        self._pending: dict[int, int] = {}
        self.actuators = (0, 0, 0)
        self.gains = {0xBD: 0xFF, 0xBE: 0xFF, 0xBF: 0xFF}
        self._expander = 0x1F
        self._bus_addr = 0
        self._id_channel = 0
        self._scan_offset = 0
        self._bulk: queue.Queue = queue.Queue()
        self._streaming = False
        self._scan_pending = False

    # -- VarianTransport ---------------------------------------------------------

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def control_out(self, request: int, value: int, index: int, data: bytes = b"") -> None:
        with self._lock:
            self.requests.append(("out", request, value, index))
            if request == p.REQ_FIRMWARE:
                if len(data) != value:
                    raise AssertionError("firmware chunk length does not match wValue")
                if not self._fw:
                    self._running = False
                    self._reset_stream()
                self._fw.extend(data)
                if self._fw.rstrip().endswith(b":00000001FF"):
                    self._running = True
                    self.firmware_uploads += 1
                    self._fw.clear()
            elif request == p.REQ_SET:
                self._regs[index] = value
                if index == p.REG_TRIGGER and value == 0xFFFF:
                    self._triggered()

    def control_in(self, request: int, value: int, index: int, length: int) -> bytes:
        with self._lock:
            self.requests.append(("in", request, value, index))
            out = self._answer(request, value, index, length)
        return out[:length].ljust(length, b"\x00")

    def bulk_read(self, size: int, timeout: float) -> bytes:
        with self._lock:
            if self._bulk.empty() and self._scan_pending:
                self._scan_pending = False
                self._queue_scan()
        try:
            data = self._bulk.get(timeout=timeout)
        except queue.Empty:
            return b""
        if self.scan_time_s and len(data) == p.SCAN_TRANSFERS[0]:
            time.sleep(self.scan_time_s)   # the mirror sweep; control requests still run
        if data == p.TERMINATOR:
            with self._lock:
                if self._streaming and self._regs.get(p.REG_CONTINUOUS) == 1:
                    self._scan_pending = True
                else:
                    self._streaming = False
        return data[:size]

    # -- bench model ----------------------------------------------------------------

    def _answer(self, request: int, value: int, index: int, length: int) -> bytes:
        if request == p.REQ_STATUS:
            if index == 1:
                return b"\x00\x02"
            return p.STATUS_FIRMWARE_RUNNING if self._running else b"\x00\x00"
        if not self._running:
            return b"\x00\x00"
        if request == p.REQ_BENCH:
            op, arg = index >> 8, index & 0xFF
            if op == 0x02:
                return bytes([self._bench_regs.get(arg, 0), 0])
            if 0x40 <= op < 0x80:   # write: 0x40 | register
                self._bench_regs[op & 0x3F] = arg
                return b"\x00\x00"
            if index == 0x030C:
                return b"\x0b\x00"
            if index == 0x0801:
                return b"\x01\x00"
            if index == 0x0802:
                return b"\x16\x00"
            if op in (0xAF, 0xB0, 0xB1, 0xBD, 0xBE, 0xBF):
                self._pending[op] = arg
            elif index == 0x0308:
                self.actuators = tuple(self._pending.get(k, v) for k, v in zip((0xB0, 0xB1, 0xAF), self.actuators))
            elif index == 0x0301:
                self.gains = {k: self._pending.get(k, v) for k, v in self.gains.items()}
            if op in (0xA2, 0xA3):
                key = "hi" if op == 0xA2 else "lo"
                self._regs[key] = arg
                raw = (self._regs.get("hi", 0) << 8) | self._regs.get("lo", 0)
                self._scan_offset = raw - 0x10000 if raw & 0x8000 else raw
            return b"\x00\x00"
        if request == p.REQ_BUS:
            if index == 0:
                self._bus_addr = value
                return b"\x00\x00" if value in (0x40, 0x41, 0x92, 0x93) else b"\x00\x04"
            if index == 2 and self._bus_addr == 0x40:
                self._expander = value
            if index == 3 and self._bus_addr == 0x41:
                return bytes([self._expander, 0])
            return b"\x00\x00"
        if request == p.REQ_ID:
            if index == 0:
                self._id_channel = value
                return b"\x00\x00" if value in (6, 7) else b"\x00\x03"
            present = self._id_channel in (6, 7)
            if index == 1:
                if not present:
                    return b"\x03" + bytes(9)
                return b"\x00\x00\x14" + bytes([self._id_channel]) * 6 + b"\x5a"
            if index == 4:
                return b"\x00\x00" + b"\xff" * 32
            return b"\x00\x00"
        return b"\x00\x00"

    def _reset_stream(self) -> None:
        self._streaming = False
        self._scan_pending = False
        while not self._bulk.empty():
            self._bulk.get_nowait()

    def _triggered(self) -> None:
        last = self._regs.get(p.REG_LAST_CHUNK)
        chunks = self._regs.get(p.REG_CHUNKS)
        if chunks == 0 and last == 2 * p.ALIGN_POINTS:
            start = p.ZPD_INDEX + _HALF - self.align_burst
            counts = self._true_counts()[start : start + p.ALIGN_POINTS] / p.GAIN_FACTOR
            self._bulk.put(self._encode(counts))
            self._bulk.put(p.TERMINATOR)
        elif chunks == 0 and last == 2112:
            self._bulk.put(self._encode(self._channel_signal()))
            self._bulk.put(p.TERMINATOR)
            self.cal_reads += 1
        elif chunks == 1 and last == p.SCAN_TRANSFERS[1]:
            self._streaming = True
            self._queue_scan()

    def _channel_signal(self) -> np.ndarray:
        """A calibration read of the selected channel: level set by the
        actuators and that channel's gain, with a little ripple."""
        channel = self._bench_regs.get(0x01, 0x07)
        reg, full = {0x0E: (0xBD, 60000.0), 0x0C: (0xBE, 52000.0), 0x0D: (0xBF, 45000.0)}.get(channel, (0xBD, 0.0))
        d2 = sum((a - b) ** 2 for a, b in zip(self.actuators, self.optimum))
        level = full * np.exp(-d2 / (2 * 40.0**2)) * (self.gains[reg] - 3) / 252.0
        ripple = 0.01 * level * np.sin(np.arange(1056) / 30.0)
        return np.minimum(level + ripple, 32767.0)

    def _queue_scan(self) -> None:
        # The scan starts `offset` samples after the alignment read started.
        start = p.ZPD_INDEX + _HALF - self.align_burst + self._scan_offset
        counts = self._true_counts()[start : start + p.SCAN_POINTS].copy()
        if counts.size < p.SCAN_POINTS:
            counts = np.pad(counts, (0, p.SCAN_POINTS - counts.size))
        zone = slice(p.GAIN_START, p.GAIN_START + p.GAIN_POINTS)
        counts[zone] /= p.GAIN_FACTOR
        raw = self._encode(counts)
        self._bulk.put(raw[: p.SCAN_TRANSFERS[0]])
        self._bulk.put(raw[p.SCAN_TRANSFERS[0] :])
        self._bulk.put(p.TERMINATOR)
        self.scans_sent += 1

    def _encode(self, counts: np.ndarray) -> bytes:
        if self.noise:
            counts = counts + self._rng.normal(0.0, self.noise, counts.size)
        return np.clip(np.round(counts), -32768, 32767).astype(">i2").tobytes()

    def _true_counts(self) -> np.ndarray:
        """A long double-sided interferogram (counts, full gain), ZPD in the
        middle at index ZPD_INDEX + _HALF."""
        n = 2 * (p.ZPD_INDEX + _HALF)
        sigma = np.fft.rfftfreq(n, d=1.0 / p.DEFAULT_LASER_WAVENUMBER)
        beam = np.exp(-(((sigma - 2600.0) / 1500.0) ** 2)) * (sigma > 400)
        scale = -270000.0 / np.abs(np.fft.irfft(beam, n=n)).max()   # open beam peaks at -270000
        if self.transmittance is not None:
            beam = beam * self.transmittance(sigma)
        return np.fft.fftshift(np.fft.irfft(beam, n=n)) * scale
