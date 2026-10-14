"""Replay real Varian 3100 captures against the driver.

Skipped unless ``OPENFTIR_VARIAN_DATA`` points at a folder holding USB
captures of Resolutions Pro (``*.pcap``), the ``.bsp`` files saved in the
same sessions and the firmware file (``*firmware*.hex``). None of that is in
the repository.

The replay transport answers each request with what the bench answered in
the capture, and fails if the driver sends anything different, in a
different order. Bulk data is only released once the driver has sent the
requests that came before it in the capture.

Run with the data:   OPENFTIR_VARIAN_DATA=/path/to/data pytest tests/test_varian_replay.py
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import pytest

from openftir.instruments.base import CollectParams, InstrumentError
from openftir.instruments.varian import VarianInstrument, scans_to_single_beam
from openftir.instruments.varian import protocol as p

_DATA = os.environ.get("OPENFTIR_VARIAN_DATA")
pytestmark = pytest.mark.skipif(not _DATA, reason="OPENFTIR_VARIAN_DATA not set -- Varian captures not available")


class ReplayMismatch(InstrumentError):
    pass


class ReplayTransport:
    def __init__(self, transfers) -> None:
        self.controls = [t for t in transfers if t.setup is not None]
        self.pos = 0
        self.broken: ReplayMismatch | None = None
        self._cond = threading.Condition()
        self._bulk = []
        for t in transfers:
            if t.setup is None:
                gate = sum(1 for c in self.controls if c.t_submit < t.t_complete)
                self._bulk.append((gate, bytes(t.data)))

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _next(self, direction: str, request: int, value: int, index: int, data: bytes = b"") -> bytes:
        with self._cond:
            if self.broken:
                raise self.broken
            if self.pos >= len(self.controls):
                self.broken = ReplayMismatch(f"request {self.pos}: capture ended, driver sent {direction} {request:#04x} {value:#06x} {index:#06x}")
                raise self.broken
            c = self.controls[self.pos]
            s = c.setup
            want = ("in" if s.direction_in else "out", s.b_request, s.w_value, s.w_index)
            got = (direction, request, value, index)
            if want != got or (direction == "out" and bytes(c.data) != data):
                self.broken = ReplayMismatch(
                    f"request {self.pos}: capture has {want[0]} {want[1]:#04x} {want[2]:#06x} {want[3]:#06x}, "
                    f"driver sent {got[0]} {got[1]:#04x} {got[2]:#06x} {got[3]:#06x}"
                )
                self._cond.notify_all()
                raise self.broken
            self.pos += 1
            self._cond.notify_all()
            return bytes(c.data)

    def control_in(self, request, value, index, length):
        return self._next("in", request, value, index)

    def control_out(self, request, value, index, data=b""):
        self._next("out", request, value, index, bytes(data))

    def bulk_read(self, size, timeout):
        with self._cond:
            if not self._bulk:
                return b""
            gate, data = self._bulk[0]
            if not self._cond.wait_for(lambda: self.pos >= gate or self.broken, timeout=min(timeout, 5.0)):
                return b""
            if self.broken:
                return b""
            self._bulk.pop(0)
            return data[:size]


def _capture(name_part: str):
    from tools.usbcap.analysis import load_capture

    pcaps = [f for f in Path(_DATA).glob("*.pcap") if name_part in f.name]
    if not pcaps:
        pytest.skip(f"no capture matching {name_part!r}")
    cap = load_capture(pcaps[0])
    device = next(t.device for t in cap.transfers if t.setup is None and len(t.data) in (4048, 1990, 2112))
    return [t for t in cap.transfers if t.device == device and not (t.setup and t.setup.b_request in (0x06, 0x09))]


def _is_status1(t) -> bool:
    return t.setup is not None and t.setup.b_request == p.REQ_STATUS and t.setup.w_index == 1


def _firmware_starts(transfers) -> list[int]:
    """Indexes of the status reads that open each firmware upload."""
    return [
        i for i in range(len(transfers) - 2)
        if _is_status1(transfers[i]) and transfers[i + 2].setup is not None
        and transfers[i + 2].setup.b_request == p.REQ_FIRMWARE
    ]


def _collections(transfers):
    """(start, end) windows of each scan collection, from the status read
    before its firmware reload to the 030D read after its last scan."""
    starts = _firmware_starts(transfers) + [len(transfers)]
    windows = []
    for a, b in zip(starts, starts[1:]):
        scans = [i for i in range(a, b) if transfers[i].setup is None and len(transfers[i].data) == 4048]
        if not scans:
            continue
        end = next(i for i in range(scans[-1], b) if transfers[i].setup is not None
                   and transfers[i].setup.b_request == p.REQ_BENCH and transfers[i].setup.w_index == 0x030D)
        windows.append((a, end + 1, len(scans)))
    return windows


def _driver(transport) -> VarianInstrument:
    fw = next(Path(_DATA).glob("*firmware*.hex"))
    inst = VarianInstrument(transport=transport, firmware=str(fw))
    inst.move_settle_s = inst.gain_settle_s = 0
    return inst


def test_connect_handshake_matches_resolutions():
    transfers = _capture("clean_1scan")
    end = _firmware_starts(transfers)[1]
    replay = ReplayTransport(transfers[:end])
    _driver(replay)._handshake()
    assert replay.pos == len(replay.controls)


@pytest.mark.parametrize("name,bsp", [("clean_1scan", "clean-1scan.bsp"), ("clean_4scan", "clean-4scan.bsp")])
def test_collections_match_resolutions(name, bsp):
    from openftir.io import _bsp

    transfers = _capture(name)
    results = []
    for start, end, nscans in _collections(transfers):
        replay = ReplayTransport(transfers[start:end])
        inst = _driver(replay)
        inst._connected = True
        scans, meta = inst._collect(CollectParams(scans=nscans, apodization="norton-beer-medium", x_min=400, x_max=7800), None, None)
        assert replay.broken is None
        assert replay.pos == len(replay.controls), "driver stopped early"
        assert meta["zpd_found"] == p.ZPD_INDEX
        results.append(scans)

    spectra = _bsp.read_bsp(Path(_DATA) / bsp)
    background = next(s for s in spectra if s.y_unit.value == "single beam")
    sample = next(s for s in spectra if s.y_unit.value == "transmittance")
    params = CollectParams(apodization="norton-beer-medium", x_min=400, x_max=7800)
    _, bg = scans_to_single_beam(results[0], params, p.DEFAULT_LASER_WAVENUMBER)
    _, sa = scans_to_single_beam(results[1], params, p.DEFAULT_LASER_WAVENUMBER)

    band = (background.x > 500) & (background.x < 4000)
    ours_bg = np.interp(background.x[band], bg.x[::-1], bg.y[::-1])
    assert np.corrcoef(ours_bg, background.y[band])[0, 1] > 0.99999
    transmittance = 100 * np.interp(sample.x, sa.x[::-1], (sa.y / bg.y)[::-1])
    assert np.sqrt(np.mean((transmittance - sample.y) ** 2)) < 0.01


@pytest.mark.parametrize("name", ["first_capture", "clean_4scan"])
def test_cold_start_matches_resolutions_into_the_fine_search(name):
    """The cold-start raster and approach are Resolutions' exactly; the fine
    hill climb is OpenFTIR's own version of Resolutions', so the replay may
    diverge partway into it, but not before its first probes."""
    transfers = _capture(name)
    starts = _firmware_starts(transfers) + [len(transfers)]
    for start, nxt in zip(starts, starts[1:]):
        reg10 = next((t for t in transfers[start:nxt] if t.setup is not None
                      and t.setup.b_request == p.REQ_BENCH and t.setup.w_index == 0x0210), None)
        if reg10 is not None and bytes(reg10.data)[:1] == b"\x00":
            break
    else:
        pytest.skip("no cold start in this capture")
    zero = next(i for i in range(start, len(transfers)) if transfers[i].setup is not None
                and transfers[i].setup.b_request == p.REQ_BENCH and transfers[i].setup.w_index == 0xB000)
    replay = ReplayTransport(transfers[start:])
    with pytest.raises(ReplayMismatch):
        _driver(replay)._prepare(None)
    fine_start = sum(1 for t in transfers[start:zero] if t.setup is not None) + 15 * 4
    # at least the first eight fine-search probes (4 requests to move, 3 to read)
    assert replay.pos >= fine_start + 8 * 7, str(replay.broken)
