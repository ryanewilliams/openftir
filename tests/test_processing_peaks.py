import numpy as np
import pytest

from openftir.processing import find_peaks, peak_area

from test_processing_synth import make_absorbance_spectrum, make_transmittance_spectrum

BANDS = [(1050.0, 0.8, 20.0), (1650.0, 0.5, 15.0), (2900.0, 0.6, 25.0)]


@pytest.mark.parametrize("descending", [True, False])
def test_find_peaks_absorbance(descending):
    s = make_absorbance_spectrum(bands=BANDS, baseline_slope=0.0, noise=0.0005, descending=descending)
    peaks = find_peaks(s, prominence=0.1)
    positions = sorted(p.position for p in peaks)
    expected = sorted(c for c, _h, _w in BANDS)
    assert len(positions) == len(expected)
    for got, exp in zip(positions, expected):
        assert abs(got - exp) < 5.0
    # sorted by position
    assert positions == [p.position for p in sorted(peaks, key=lambda p: p.position)]


def test_find_peaks_transmittance_dips():
    # noise is in absorbance units before the %T conversion, where its effect
    # is exponentially amplified -- keep it modest (see test_processing_baseline.py)
    s = make_transmittance_spectrum(bands=BANDS, baseline_slope=0.0, noise=0.002)
    peaks = find_peaks(s, prominence=5.0)
    positions = sorted(p.position for p in peaks)
    expected = sorted(c for c, _h, _w in BANDS)
    assert len(positions) == len(expected)
    for got, exp in zip(positions, expected):
        assert abs(got - exp) < 5.0
    # heights should reflect the dip value (below 100), not inverted
    for p in peaks:
        assert p.height < 100.0


def test_find_peaks_min_distance_merges_close_peaks():
    s = make_absorbance_spectrum(bands=[(1000.0, 0.5, 10.0), (1010.0, 0.5, 10.0)], baseline_slope=0.0, noise=0.0)
    peaks_close = find_peaks(s, prominence=0.05, min_distance_cm=1.0)
    peaks_far = find_peaks(s, prominence=0.05, min_distance_cm=100.0)
    assert len(peaks_far) <= len(peaks_close)


def test_peak_area_positive_and_scales_with_height():
    s1 = make_absorbance_spectrum(bands=[(1500.0, 0.5, 30.0)], baseline_slope=0.0, noise=0.0)
    s2 = make_absorbance_spectrum(bands=[(1500.0, 1.0, 30.0)], baseline_slope=0.0, noise=0.0)
    a1 = peak_area(s1, 1400.0, 1600.0)
    a2 = peak_area(s2, 1400.0, 1600.0)
    assert a1 > 0
    assert a2 > a1 * 1.5


def test_peak_area_requires_enough_points():
    s = make_absorbance_spectrum(n=50)
    with pytest.raises(ValueError):
        peak_area(s, 1000.0, 1000.001)
