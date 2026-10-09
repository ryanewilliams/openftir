"""Tests for ``openftir.instruments.summit.driver.SummitInstrument`` against
:class:`~openftir.instruments.summit.fake.FakeSummitTransport` -- no real
hardware is touched anywhere in this file.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from openftir.instruments.base import CollectionCancelled, CollectParams, InstrumentError
from openftir.instruments.summit.driver import SummitInstrument
from openftir.instruments.summit.fake import FakeSummitTransport
from openftir.spectrum import XUnit, YUnit
from test_instruments_contract import run_driver_contract

# -- full driver contract (shared with every other driver) --------------------


def test_summit_driver_contract():
    run_driver_contract(lambda: SummitInstrument(transport=FakeSummitTransport()))


def test_summit_has_no_set_sample():
    # The polystyrene calibration check in the shared contract is (correctly)
    # skipped for this driver -- confirm that's actually because it has no
    # virtual sample stage, not an accident.
    assert getattr(SummitInstrument(), "set_sample", None) is None


# -- connect() ------------------------------------------------------------------


def test_connect_populates_info_from_fake_bench():
    inst = SummitInstrument(transport=FakeSummitTransport())
    info = inst.connect()
    assert info.vendor == "Thermo Fisher Scientific"
    assert "142" in info.model
    assert info.laser_wavenumber == pytest.approx(2 * 11685.125977)
    assert info.resolutions[0] == 4.0
    assert set(info.resolutions) == {4.0, 8.0, 16.0, 2.0}
    assert info.extra["unverified_resolutions"] == (16.0, 2.0)
    inst.disconnect()


def test_connect_sends_one_combined_get():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    get_commands = [c for c in transport.commands if "<get>" in c]
    # One combined get for the connect-time keys, one for the diagnostic
    # Cfg ranges.
    assert len(get_commands) == 2
    assert "<benchModel/>" in get_commands[0]
    assert "<serialNum/>" in get_commands[0]


# -- diagnostics() --------------------------------------------------------------


def test_diagnostics_ok_when_connected_and_in_range():
    inst = SummitInstrument(transport=FakeSummitTransport())
    inst.connect()
    diag = inst.diagnostics()
    assert diag.ok
    assert "plus5v" in diag.items
    inst.disconnect()


def test_diagnostics_disconnected():
    inst = SummitInstrument(transport=FakeSummitTransport())
    diag = inst.diagnostics()
    assert not diag.ok


def test_diagnostics_flags_out_of_range_value():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport._state["plus5v"] = "9.9"  # way outside the 4.5-5.5 Cfg range
    diag = inst.diagnostics()
    assert not diag.ok
    assert "OUT OF RANGE" in diag.items["plus5v"]
    inst.disconnect()


# -- collect_single_beam() / collect_interferogram() ---------------------------


def test_collect_interferogram_length_matches_2x_peakpos():
    inst = SummitInstrument(transport=FakeSummitTransport())
    inst.connect()
    spec = inst.collect_interferogram(CollectParams(scans=3, resolution=4.0))
    assert spec.y_unit == YUnit.INTERFEROGRAM
    assert spec.x_unit == XUnit.POINTS
    assert len(spec) == 2 * 6154
    assert spec.meta["peakPos"] == 6154
    assert spec.meta["sdp"] == 12426
    assert len(spec.meta["scan_ids"]) == 3
    inst.disconnect()


def test_collect_single_beam_meta():
    inst = SummitInstrument(transport=FakeSummitTransport())
    inst.connect()
    params = CollectParams(scans=4, resolution=4.0, x_min=600, x_max=4000)
    spec = inst.collect_single_beam(params)
    assert spec.meta["driver"] == "summit"
    assert spec.meta["scans"] == 4
    assert spec.meta["resolution"] == 4.0
    assert spec.meta["laserFreq"] == pytest.approx(11685.125977)
    inst.disconnect()


# -- exact command sequence (docs/summit-protocol.md "Connection sequence" /
#    "Collecting scans") ----------------------------------------------------


def _expected_collect_sequence(peak_pos=6154, sdp=12426, gain=1):
    return [
        "<cmd><set><sampleshuttle>1</sampleshuttle></set></cmd>",
        f"<cmd><set><ssp>1</ssp><fwdVel>0.4747</fwdVel><peakPos>{peak_pos}</peakPos>"
        f"<sdp>{sdp}</sdp><detGain>{gain}</detGain></set></cmd>",
        "<cmd><set><hpf>20</hpf></set></cmd>",
        "<cmd><set><lpf>11000</lpf></set></cmd>",
        "<cmd><set><rtrVel>4</rtrVel></set></cmd>",
        "<cmd><set><bsSlot>0</bsSlot><beampathId>1</beampathId><sourceId>2</sourceId><aperture>100</aperture></set></cmd>",
        "<cmd><set><settleTime>200</settleTime><bidirScan>0</bidirScan></set></cmd>",
        "<cmd><get><motorsDone/></get></cmd>",
        "<cmd><set><stepScanState>0</stepScanState><collectState>0</collectState>"
        "<servoState>1</servoState><extTrigger>0</extTrigger></set></cmd>",
        "<cmd><set><lightbar>4</lightbar></set></cmd>",
        "<cmd><set><stepScanState>0</stepScanState><collectState>1</collectState>"
        "<servoState>1</servoState><extTrigger>0</extTrigger></set></cmd>",
    ]


def test_exact_command_sequence_matches_paradigm():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport.commands.clear()

    inst.collect_single_beam(CollectParams(scans=2, resolution=4.0))

    expected_start = _expected_collect_sequence()
    assert transport.commands[: len(expected_start)] == expected_start
    assert transport.commands[-2:] == [
        "<cmd><set><stepScanState>0</stepScanState><collectState>0</collectState>"
        "<servoState>1</servoState><extTrigger>0</extTrigger></set></cmd>",
        "<cmd><set><lightbar>1</lightbar></set></cmd>",
    ]
    inst.disconnect()


def test_exact_command_sequence_with_manual_gain_and_8cm():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport.commands.clear()

    inst.collect_single_beam(CollectParams(scans=1, resolution=8.0, gain="4"))

    expected_start = _expected_collect_sequence(peak_pos=3124, sdp=6367, gain=4)
    assert transport.commands[: len(expected_start)] == expected_start
    inst.disconnect()


def test_never_sends_motor_or_i2c_or_valwheelpos_or_mapping_commands():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    inst.collect_single_beam(CollectParams(scans=2, resolution=4.0))
    inst.diagnostics()
    inst.disconnect()
    forbidden = ("motorId", "valWheelPos", "i2cMsg", "mapping")
    for xml in transport.commands:
        for word in forbidden:
            assert word not in xml, f"forbidden command sent: {xml}"


# -- collectState 0 always sent on cancel / error ------------------------------


class _FlipAfter:
    """``is_set()`` returns False the first ``n`` calls, then True."""

    def __init__(self, n: int) -> None:
        self._n = n
        self._count = 0

    def is_set(self) -> bool:
        self._count += 1
        return self._count > self._n


def test_cancel_mid_collection_still_stops_collection():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport.commands.clear()

    cancel = _FlipAfter(n=3)  # lets setup + motorsDone + one read-loop pass through
    with pytest.raises(CollectionCancelled):
        inst.collect_single_beam(CollectParams(scans=1000, resolution=4.0), cancel=cancel)

    assert transport.commands[-2:] == [
        "<cmd><set><stepScanState>0</stepScanState><collectState>0</collectState>"
        "<servoState>1</servoState><extTrigger>0</extTrigger></set></cmd>",
        "<cmd><set><lightbar>1</lightbar></set></cmd>",
    ]
    inst.disconnect()


def test_cancel_before_collection_sends_no_commands_at_all():
    transport = FakeSummitTransport()
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport.commands.clear()

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CollectionCancelled):
        inst.collect_single_beam(CollectParams(scans=10, resolution=4.0), cancel=cancel)
    assert transport.commands == []
    inst.disconnect()


class _FailAfterReads(FakeSummitTransport):
    """Behaves like the fake transport, except ``read_data`` fails after
    ``fail_after`` successful calls -- simulates a USB error mid-collection."""

    def __init__(self, fail_after: int = 1, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fail_after = fail_after
        self._read_calls = 0

    def read_data(self, size: int, timeout: float = 5.0) -> bytes:
        self._read_calls += 1
        if self._read_calls > self._fail_after:
            raise InstrumentError("simulated USB read failure")
        return super().read_data(size, timeout=timeout)


def test_transport_error_mid_collection_still_stops_collection():
    transport = _FailAfterReads(fail_after=2)
    inst = SummitInstrument(transport=transport)
    inst.connect()
    transport.commands.clear()

    with pytest.raises(InstrumentError, match="simulated USB read failure"):
        inst.collect_single_beam(CollectParams(scans=1000, resolution=4.0))

    assert transport.commands[-2:] == [
        "<cmd><set><stepScanState>0</stepScanState><collectState>0</collectState>"
        "<servoState>1</servoState><extTrigger>0</extTrigger></set></cmd>",
        "<cmd><set><lightbar>1</lightbar></set></cmd>",
    ]
    inst.disconnect()


def test_stop_collection_is_attempted_even_if_collectstate_0_itself_fails():
    class _AlsoFailsToStop(_FailAfterReads):
        def command(self, xml, timeout=5.0):
            if "<collectState>1</collectState>" in xml:
                self._started = True
            if "<collectState>0</collectState>" in xml and getattr(self, "_started", False):
                raise InstrumentError("simulated: can't even stop")
            return super().command(xml, timeout=timeout)

    transport = _AlsoFailsToStop(fail_after=2)
    inst = SummitInstrument(transport=transport)
    inst.connect()

    # The original read failure must still propagate (not be masked by the
    # failure while trying to stop).
    with pytest.raises(InstrumentError, match="simulated USB read failure"):
        inst.collect_single_beam(CollectParams(scans=1000, resolution=4.0))
    inst.disconnect()


# -- corrupted scans are dropped but collection still completes ----------------


def test_corrupted_scans_are_dropped_and_collection_still_succeeds():
    transport = FakeSummitTransport(corrupt_scan_ids={2, 4})
    inst = SummitInstrument(transport=transport)
    inst.connect()
    params = CollectParams(scans=5, resolution=4.0)
    spec = inst.collect_single_beam(params)
    assert spec.meta["scans"] == 5
    assert len(spec.meta["scan_ids"]) == 5
    assert 2 not in spec.meta["scan_ids"]
    assert 4 not in spec.meta["scan_ids"]
    inst.disconnect()


# -- progress / cancel contract already covered by run_driver_contract, plus: -


def test_progress_called_exactly_scans_times():
    inst = SummitInstrument(transport=FakeSummitTransport())
    inst.connect()
    calls: list[tuple[int, int]] = []
    inst.collect_single_beam(CollectParams(scans=6, resolution=4.0), progress=lambda a, b: calls.append((a, b)))
    assert calls == [(i, 6) for i in range(1, 7)]
    inst.disconnect()


def test_coadd_helpers_are_standalone(monkeypatch=None):
    from openftir.instruments.summit.driver import coadd_interferogram, coadd_to_single_beam

    peak_pos = 10
    scans = [np.full(25, float(i)) for i in range(3)]
    igram = coadd_interferogram(scans, peak_pos)
    assert igram.shape == (2 * peak_pos,)
    assert np.allclose(igram, 1.0)  # mean of 0,1,2

    params = CollectParams(scans=3, resolution=4.0, x_min=400, x_max=4000)
    scans_full = [np.sin(np.linspace(0, 50, 2 * peak_pos)) for _ in range(3)]
    _igram, sb = coadd_to_single_beam(scans_full, peak_pos, 11685.125977, params)
    assert sb.y_unit == YUnit.SINGLE_BEAM
