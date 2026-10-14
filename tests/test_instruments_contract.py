"""Reusable instrument-driver contract.

``run_driver_contract`` exercises the parts of the :class:`~openftir.instruments.base.Instrument`
interface every driver is expected to honour. Any new driver (real hardware
included) can reuse it: build a small ``test_drivers_<name>.py`` and call
``run_driver_contract(lambda: YourDriver(...))``.

A couple of checks (the polystyrene calibration ratio, since it needs a way
to put a known sample on the stage) are skipped automatically for drivers
that don't expose :meth:`set_sample` -- real hardware would run the
equivalent check as a manual/interactive test with an actual polystyrene
film instead.
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np
import pytest

from openftir.instruments.base import (
    CollectionCancelled,
    CollectParams,
    Instrument,
    InstrumentInfo,
)
from openftir.processing import find_peaks, ratio
from openftir.spectrum import XUnit, YUnit

InstrumentFactory = Callable[[], Instrument]

#: (expected peak position cm-1, max allowed distance to the nearest found peak)
_POLYSTYRENE_PEAKS = (1601.0, 1493.0, 1452.0, 698.0)
_PEAK_TOLERANCE_CM = 15.0


def _check_connect_disconnect(factory: InstrumentFactory) -> None:
    instrument = factory()
    assert not instrument.connected
    info = instrument.connect()
    assert isinstance(info, InstrumentInfo)
    assert instrument.connected
    instrument.disconnect()
    assert not instrument.connected


def _check_info(factory: InstrumentFactory) -> None:
    instrument = factory()
    info = instrument.connect()
    try:
        assert info.vendor
        assert info.model
        assert info.laser_wavenumber > 0
        assert len(info.resolutions) > 0
    finally:
        instrument.disconnect()


def _resolution(info: InstrumentInfo, preferred: float) -> float:
    """``preferred`` if the driver offers it, else its first advertised resolution."""
    return preferred if preferred in info.resolutions else info.resolutions[0]


def _check_diagnostics(factory: InstrumentFactory) -> None:
    instrument = factory()
    instrument.connect()
    try:
        diag = instrument.diagnostics()
        assert isinstance(diag.ok, bool)
        assert isinstance(diag.items, dict)
    finally:
        instrument.disconnect()


def _check_collect_single_beam(factory: InstrumentFactory) -> None:
    instrument = factory()
    info = instrument.connect()
    try:
        params = CollectParams(scans=2, resolution=_resolution(info, 16.0), x_min=450.0, x_max=3800.0)
        spec = instrument.collect_single_beam(params)
        assert spec.y_unit == YUnit.SINGLE_BEAM
        assert spec.x_unit == XUnit.WAVENUMBER
        assert spec.x.size > 0
        assert np.all(spec.x >= params.x_min) and np.all(spec.x <= params.x_max)
        if spec.x.size > 1:
            assert spec.x[0] > spec.x[-1], "single-beam spectrum must be in descending wavenumber order"
    finally:
        instrument.disconnect()


def _check_progress(factory: InstrumentFactory) -> None:
    instrument = factory()
    info = instrument.connect()
    try:
        calls: list[tuple[int, int]] = []
        params = CollectParams(scans=5, resolution=_resolution(info, 16.0))
        instrument.collect_single_beam(params, progress=lambda done, total: calls.append((done, total)))
        assert len(calls) == params.scans
        assert calls[-1] == (params.scans, params.scans)
        assert [c[0] for c in calls] == list(range(1, params.scans + 1))
    finally:
        instrument.disconnect()


def _check_cancel(factory: InstrumentFactory) -> None:
    instrument = factory()
    info = instrument.connect()
    try:
        cancel = threading.Event()
        cancel.set()
        params = CollectParams(scans=10, resolution=_resolution(info, 16.0))
        with pytest.raises(CollectionCancelled):
            instrument.collect_single_beam(params, cancel=cancel)
    finally:
        instrument.disconnect()


def _check_noise_falls_with_scans(factory: InstrumentFactory) -> None:
    instrument = factory()
    info = instrument.connect()
    try:
        params_low = CollectParams(scans=2, resolution=_resolution(info, 8.0))
        params_high = CollectParams(scans=32, resolution=_resolution(info, 8.0))

        # Two independent collections with identical params should agree
        # almost exactly except for detector noise; differencing them cancels
        # the (near-identical) underlying spectral shape and isolates the
        # noise, which must shrink as more scans are co-added.
        low_a = instrument.collect_single_beam(params_low)
        low_b = instrument.collect_single_beam(params_low)
        high_a = instrument.collect_single_beam(params_high)
        high_b = instrument.collect_single_beam(params_high)

        noise_low = float(np.std(low_a.y - low_b.y))
        noise_high = float(np.std(high_a.y - high_b.y))
        assert noise_high < noise_low * 0.7, (noise_low, noise_high)
    finally:
        instrument.disconnect()


def _check_polystyrene_calibration_ratio(factory: InstrumentFactory) -> None:
    instrument = factory()
    instrument.connect()
    try:
        set_sample = getattr(instrument, "set_sample", None)
        if not callable(set_sample):
            pytest.skip("driver has no set_sample(); skipping the polystyrene calibration check")

        params = CollectParams(scans=16, resolution=4.0, x_min=400.0, x_max=4000.0)
        set_sample("none")
        background = instrument.collect_single_beam(params)
        set_sample("polystyrene")
        sample = instrument.collect_single_beam(params)

        absorbance = ratio(sample, background, to=YUnit.ABSORBANCE)
        peaks = find_peaks(absorbance, prominence=0.05)
        positions = [p.position for p in peaks]
        assert positions, "expected at least one peak in the polystyrene ratio"

        for expected in _POLYSTYRENE_PEAKS:
            nearest = min(positions, key=lambda p: abs(p - expected))
            assert abs(nearest - expected) <= _PEAK_TOLERANCE_CM, (
                f"no peak found near {expected} cm-1 (nearest was {nearest} cm-1)"
            )
    finally:
        instrument.disconnect()


def run_driver_contract(instrument_factory: InstrumentFactory) -> None:
    """Run the full driver contract against ``instrument_factory()`` instances.

    ``instrument_factory`` must return a fresh, *unconnected* instrument each
    time it's called.
    """
    _check_connect_disconnect(instrument_factory)
    _check_info(instrument_factory)
    _check_diagnostics(instrument_factory)
    _check_collect_single_beam(instrument_factory)
    _check_progress(instrument_factory)
    _check_cancel(instrument_factory)
    _check_noise_falls_with_scans(instrument_factory)
    _check_polystyrene_calibration_ratio(instrument_factory)


def test_simulated_instrument_contract() -> None:
    from openftir.instruments.simulated import SimulatedInstrument

    run_driver_contract(lambda: SimulatedInstrument())
