"""Varian 3100 driver against the in-memory fake bench (no hardware, no
Varian firmware: a made-up Intel HEX file stands in for it)."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from openftir.instruments import registry
from openftir.instruments.base import CollectionCancelled, CollectParams, InstrumentError, InstrumentFileError
from openftir.instruments.varian import VarianInstrument
from openftir.instruments.varian import calibration as cal
from openftir.instruments.varian import protocol as p
from openftir.instruments.varian.fake import FakeVarianTransport, synthetic_firmware


@pytest.fixture
def firmware(tmp_path):
    path = tmp_path / "bench.hex"
    path.write_bytes(synthetic_firmware())
    return str(path)


def _instrument(firmware, **fake_kwargs) -> tuple[VarianInstrument, FakeVarianTransport]:
    fake = FakeVarianTransport(**fake_kwargs)
    inst = VarianInstrument(transport=fake, firmware=firmware)
    inst.move_settle_s = inst.gain_settle_s = 0
    return inst, fake


def _params(**kwargs) -> CollectParams:
    return CollectParams(**{"apodization": "norton-beer-medium", "scans": 1, **kwargs})


def test_registered():
    assert "varian" in registry.available_drivers()


def test_firmware_file_is_required(monkeypatch):
    monkeypatch.delenv(p.FIRMWARE_ENV, raising=False)
    with pytest.raises(InstrumentFileError) as err:
        VarianInstrument(transport=FakeVarianTransport())
    assert err.value.key == "firmware"


def test_firmware_from_environment(monkeypatch, firmware):
    monkeypatch.setenv(p.FIRMWARE_ENV, firmware)
    VarianInstrument(transport=FakeVarianTransport())


@pytest.mark.parametrize("content", [b"hello world" * 200, synthetic_firmware().replace(b":00000001FF", b""),
                                     synthetic_firmware().replace(b":10000000", b":10000001", 1)])
def test_bad_firmware_files_are_refused(tmp_path, content):
    path = tmp_path / "x.hex"
    path.write_bytes(content)
    with pytest.raises(InstrumentFileError):
        VarianInstrument(transport=FakeVarianTransport(), firmware=str(path))


def test_transfer_layout_matches_resolutions():
    assert p.transfer_layout(1990) == (0, 0, 1990)
    assert p.transfer_layout(2112) == (0, 0, 2112)
    assert p.transfer_layout(8144) == (1, 4096, 4048)


def test_connect_uploads_firmware_and_reports_the_bench(firmware):
    inst, fake = _instrument(firmware)
    info = inst.connect()
    assert inst.connected and fake.firmware_uploads == 2   # handshake + setup, as Resolutions
    assert info.vendor == "Varian" and info.resolutions == (4.0,)
    assert info.laser_wavenumber == pytest.approx(p.DEFAULT_LASER_WAVENUMBER)
    assert inst.calibration is None   # warm bench: no alignment
    inst.disconnect()
    assert not fake.is_open


def test_single_scan_collection(firmware):
    inst, fake = _instrument(firmware)
    inst.connect()
    sb = inst.collect_single_beam(_params())
    assert fake.scans_sent == 1
    assert sb.meta["zpd_found"] == p.ZPD_INDEX and sb.meta["scans"] == 1
    assert sb.x[0] > sb.x[-1] and sb.y.max() > 0
    # never left scanning
    sets = [(v, i) for d, r, v, i in fake.requests if d == "out" and r == p.REQ_SET and i == p.REG_CONTINUOUS]
    assert sets[-1] == (0, p.REG_CONTINUOUS)


def test_multi_scan_collection_stops_before_the_last_scan(firmware):
    inst, fake = _instrument(firmware, scan_time_s=0.02)
    inst.connect()
    done = []
    inst.collect_single_beam(_params(scans=4), progress=lambda n, total: done.append((n, total)))
    assert done == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert fake.scans_sent == 4


def test_scan_offset_comes_from_the_alignment_read(firmware):
    inst, fake = _instrument(firmware, align_burst=409)
    inst.connect()
    sb = inst.collect_single_beam(_params())
    assert sb.meta["scan_offset"] == 409 - 516 and sb.meta["zpd_found"] == p.ZPD_INDEX
    sent = [i for d, r, v, i in fake.requests if r == p.REQ_BENCH and i >> 8 in (0xA2, 0xA3)]
    assert sent[-2:] == [0xA395, 0xA2FF]   # -107 as Resolutions sends it (low byte first)


def test_absorption_band_comes_through(firmware):
    inst, fake = _instrument(firmware)
    inst.connect()
    background = inst.collect_single_beam(_params())
    fake.transmittance = lambda s: 1 - 0.5 * np.exp(-(((s - 1700) / 12) ** 2))
    sample = inst.collect_single_beam(_params())
    t = sample.y / background.y
    assert t[np.argmin(abs(sample.x - 1700))] == pytest.approx(0.5, abs=0.05)
    assert t[np.argmin(abs(sample.x - 2600))] == pytest.approx(1.0, abs=0.01)


def test_cancel_stops_the_bench(firmware):
    inst, fake = _instrument(firmware, scan_time_s=0.05)
    inst.connect()
    cancel = threading.Event()

    def progress(n, total):
        if n == 2:
            cancel.set()

    with pytest.raises(CollectionCancelled):
        inst.collect_single_beam(_params(scans=10), progress=progress, cancel=cancel)
    assert fake.scans_sent < 10
    last_set = [(v, i) for d, r, v, i in fake.requests if d == "out" and r == p.REQ_SET and i == p.REG_CONTINUOUS][-1]
    assert last_set == (0, p.REG_CONTINUOUS)
    inst.collect_single_beam(_params())   # and the next collection still works


def test_only_4_wavenumbers_resolution(firmware):
    inst, _ = _instrument(firmware)
    inst.connect()
    with pytest.raises(InstrumentError, match="4 cm-1"):
        inst.collect_single_beam(_params(resolution=8.0))


def test_no_signal_is_reported(firmware):
    inst, _ = _instrument(firmware, transmittance=lambda s: 0 * s)
    inst.connect()
    with pytest.raises(InstrumentError, match="no interferogram signal"):
        inst.collect_single_beam(_params())


def test_cold_bench_is_aligned_on_connect(firmware):
    inst, fake = _instrument(firmware, cold=True, optimum=(0xB3, 0x7E, 0x69))
    messages = []
    inst.on_status = messages.append
    inst.connect()
    result = inst.calibration
    assert result is not None and fake._bench_regs[0x10] == 0x08
    assert all(abs(a - b) <= 3 for a, b in zip(result.point, fake.optimum))
    assert any("aligning" in m.lower() for m in messages)
    # every channel ends near the target level
    for channel, reg in cal.GAIN_CHANNELS:
        fake._bench_regs[0x01] = channel
        level = fake._channel_signal().mean()
        assert cal.LEVEL_WINDOW[0] - 300 <= level <= cal.LEVEL_WINDOW[1] + 300, (channel, level)
    # register 0x0E: bit 0 cleared while cold, set again afterwards
    assert fake._bench_regs[0x0E] == 0x89
    # and the next collection doesn't align again
    reads = fake.cal_reads
    inst.collect_single_beam(_params())
    assert fake.cal_reads == reads


def test_settings_stay_in_resolutions_range(firmware):
    inst, fake = _instrument(firmware, cold=True, optimum=(0x10, 0xF0, 0x40))   # optimum at the edge
    inst.connect()
    lo, hi = cal.SETTING_RANGE
    moves = [i & 0xFF for d, r, v, i in fake.requests if r == p.REQ_BENCH and i >> 8 in (0xB0, 0xB1, 0xAF)]
    assert all(v == 0 or lo <= v <= hi for v in moves)


def test_calibration_halves_gain_when_the_signal_is_near_the_limit():
    readings = iter([cal.Reading(30000, 29000)] * 2 + [cal.Reading(15000, 14000)] * 500)
    gains_seen = []
    c = cal.Calibrator(
        move=lambda point: None,
        set_gains=lambda gains, force=False: gains_seen.append(dict(gains)),
        select=lambda channel: None,
        read=lambda: next(readings),
    )
    c.run()
    assert gains_seen[0][0xBD] == 0xFF and gains_seen[1][0xBD] == 0x80


def test_approach_retraces_the_raster():
    path = cal.approach_path(21)
    assert path[:3] == [(0, 0, 0), cal.RASTER[0], cal.RASTER[9]]
    assert path[3:] == list(cal.RASTER[9:22])


def test_driver_contract(firmware):
    from test_instruments_contract import run_driver_contract

    def factory():
        inst = VarianInstrument(transport=FakeVarianTransport(noise=20.0), firmware=firmware)
        inst.move_settle_s = inst.gain_settle_s = 0
        return inst

    run_driver_contract(factory)
