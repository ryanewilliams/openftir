import numpy as np
import pytest

from openftir.processing import auto_label_peaks, find_peaks, peak_area, to_absorbance
from openftir.spectrum import Spectrum, XUnit, YUnit

from test_processing_synth import make_absorbance_spectrum, make_transmittance_spectrum

BANDS = [(1050.0, 0.8, 20.0), (1650.0, 0.5, 15.0), (2900.0, 0.6, 25.0)]

# Bands for auto_label_peaks: 2917 and 1735 deep (dip well below the default
# 85 %T threshold), 1450 deep too but at/below the default 1500 cm-1 cutoff,
# 3300 shallow (stays above the %T threshold).
_AUTO_LABEL_BANDS = [(2917.0, 0.9, 20.0), (1735.0, 0.8, 15.0), (1450.0, 0.9, 15.0), (3300.0, 0.06, 30.0)]


def _auto_label_transmittance_spectrum():
    return make_transmittance_spectrum(bands=_AUTO_LABEL_BANDS, baseline_slope=0.0, noise=0.0005)


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


# -- auto_label_peaks ---------------------------------------------------------------------


def _assert_positions_close(positions, expected, tol=5.0):
    positions = sorted(positions)
    expected = sorted(expected)
    assert len(positions) == len(expected)
    for got, exp in zip(positions, expected):
        assert abs(got - exp) < tol


def test_auto_label_peaks_picks_only_deep_bands_above_cutoff():
    s = _auto_label_transmittance_spectrum()
    peaks = auto_label_peaks(s)
    _assert_positions_close([p.position for p in peaks], [1735.0, 2917.0])


def test_auto_label_peaks_same_positions_in_absorbance():
    s = _auto_label_transmittance_spectrum()
    s_abs = to_absorbance(s)
    assert s_abs.y_unit == YUnit.ABSORBANCE

    positions_t = sorted(p.position for p in auto_label_peaks(s))
    positions_a = sorted(p.position for p in auto_label_peaks(s_abs))
    assert len(positions_t) == len(positions_a) == 2
    for t, a in zip(positions_t, positions_a):
        assert abs(t - a) < 1e-6
    _assert_positions_close(positions_t, [1735.0, 2917.0])


def test_auto_label_peaks_sorted_descending_wavenumber():
    s = _auto_label_transmittance_spectrum()
    peaks = auto_label_peaks(s)
    positions = [p.position for p in peaks]
    assert positions == sorted(positions, reverse=True)


def test_auto_label_peaks_lowering_cutoff_includes_band_below_old_cutoff():
    s = _auto_label_transmittance_spectrum()
    peaks = auto_label_peaks(s, min_wavenumber=1000.0)
    _assert_positions_close([p.position for p in peaks], [1450.0, 1735.0, 2917.0])


def test_auto_label_peaks_raising_threshold_includes_shallow_band():
    s = _auto_label_transmittance_spectrum()
    peaks = auto_label_peaks(s, max_transmittance=95.0)
    _assert_positions_close([p.position for p in peaks], [1735.0, 2917.0, 3300.0])


def test_auto_label_peaks_nothing_at_or_below_cutoff():
    s = _auto_label_transmittance_spectrum()
    peaks = auto_label_peaks(s, min_wavenumber=1000.0)
    assert all(p.position > 1000.0 for p in peaks)


def test_auto_label_peaks_rejects_single_beam():
    x = np.linspace(4000.0, 400.0, 500)
    s = Spectrum(x=x, y=np.ones_like(x), x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM)
    with pytest.raises(ValueError):
        auto_label_peaks(s)


def test_auto_label_peaks_rejects_interferogram():
    x = np.arange(500.0)
    s = Spectrum(x=x, y=np.zeros_like(x), x_unit=XUnit.POINTS, y_unit=YUnit.INTERFEROGRAM)
    with pytest.raises(ValueError):
        auto_label_peaks(s)
