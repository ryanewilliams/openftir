"""Driver-specific tests for :class:`openftir.instruments.simulated.SimulatedInstrument`."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from openftir.instruments.base import CollectionCancelled, CollectParams, InstrumentError
from openftir.instruments.simulated import SimulatedInstrument
from openftir.spectrum import XUnit, YUnit


def test_fail_connect_raises_and_stays_disconnected():
    instrument = SimulatedInstrument(fail_connect=True)
    with pytest.raises(InstrumentError):
        instrument.connect()
    assert not instrument.connected


def test_set_sample_validates_name():
    instrument = SimulatedInstrument()
    instrument.set_sample("water-vapour")
    assert instrument.sample == "water-vapour"
    with pytest.raises(ValueError):
        instrument.set_sample("not-a-real-sample")


def test_collect_requires_connection():
    instrument = SimulatedInstrument()
    with pytest.raises(InstrumentError):
        instrument.collect_single_beam(CollectParams(scans=1))


def test_collect_interferogram_shape_and_units():
    instrument = SimulatedInstrument(seed=0)
    instrument.connect()
    params = CollectParams(scans=4, resolution=4.0)
    igram = instrument.collect_interferogram(params)
    assert igram.y_unit == YUnit.INTERFEROGRAM
    assert igram.x_unit == XUnit.POINTS
    assert igram.x.size == igram.y.size
    assert igram.x.size > 0
    assert igram.meta["scans"] == 4
    assert igram.meta["driver"] == "simulated"


def test_collect_single_beam_meta_fields():
    instrument = SimulatedInstrument(seed=1, noise=0.001)
    instrument.connect()
    params = CollectParams(scans=8, resolution=4.0, apodization="happ-genzel", zero_fill=2, gain="auto")
    spec = instrument.collect_single_beam(params)
    for key in ("scans", "resolution", "apodization", "gain", "driver", "sample"):
        assert key in spec.meta, key
    assert spec.meta["scans"] == 8
    assert spec.meta["driver"] == "simulated"
    assert spec.meta["sample"] == "polystyrene"


def test_cancel_raises_collection_cancelled():
    instrument = SimulatedInstrument(seed=0)
    instrument.connect()
    cancel = threading.Event()

    seen = []

    def progress(done, total):
        seen.append(done)
        if done == 2:
            cancel.set()

    with pytest.raises(CollectionCancelled):
        instrument.collect_single_beam(CollectParams(scans=10, resolution=16.0), progress=progress, cancel=cancel)
    assert seen[-1] == 2


def test_scan_time_s_adds_real_delay():
    instrument = SimulatedInstrument(seed=0, scan_time_s=0.05)
    instrument.connect()
    t0 = time.perf_counter()
    instrument.collect_single_beam(CollectParams(scans=3, resolution=16.0))
    elapsed = time.perf_counter() - t0
    assert elapsed >= 3 * 0.05 * 0.8


def test_default_collection_is_fast():
    instrument = SimulatedInstrument(seed=0)
    instrument.connect()
    params = CollectParams(scans=16, resolution=4.0)
    t0 = time.perf_counter()
    instrument.collect_single_beam(params)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"16-scan collect at 4 cm-1 took {elapsed:.3f}s"


def test_diagnostics_reports_disconnected_when_not_connected():
    instrument = SimulatedInstrument()
    diag = instrument.diagnostics()
    assert diag.ok is False


def test_background_vs_none_sample_is_flat_ratio():
    """Ratioing two 'none' (empty-beam) collections should be close to 0 absorbance,
    aside from small atmospheric drift/noise residuals."""
    instrument = SimulatedInstrument(sample="none", seed=3, noise=0.0005)
    instrument.connect()
    params = CollectParams(scans=32, resolution=4.0, x_min=800.0, x_max=3500.0)
    a = instrument.collect_single_beam(params)
    b = instrument.collect_single_beam(params)

    from openftir.processing import ratio

    absorbance = ratio(a, b, to=YUnit.ABSORBANCE)
    assert np.max(np.abs(absorbance.y)) < 0.2
