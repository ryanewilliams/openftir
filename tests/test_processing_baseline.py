import numpy as np
import pytest

from openftir.processing import baseline_polynomial, baseline_als, baseline_rubberband

from test_processing_synth import make_absorbance_spectrum, make_transmittance_spectrum


def _baseline_rms(spectrum, bands):
    """RMS of the spectrum away from known band centers (should be ~baseline)."""
    mask = np.ones_like(spectrum.x, dtype=bool)
    for center, _height, width in bands:
        mask &= np.abs(spectrum.x - center) > width * 2
    return float(np.sqrt(np.mean(spectrum.y[mask] ** 2)))


BANDS = [(1050.0, 0.8, 20.0), (1650.0, 0.5, 15.0), (2900.0, 0.6, 25.0)]


@pytest.mark.parametrize("descending", [True, False])
@pytest.mark.parametrize(
    "method",
    [
        lambda s: baseline_polynomial(s, degree=3),
        lambda s: baseline_als(s, lam=1e6, p=0.01),
        lambda s: baseline_rubberband(s),
    ],
)
def test_baseline_methods_reduce_rms_absorbance(descending, method):
    s = make_absorbance_spectrum(bands=BANDS, baseline_slope=0.0006, baseline_curve=0.3, noise=0.002, descending=descending)
    before = _baseline_rms(s, BANDS)
    corrected = method(s)
    after = _baseline_rms(corrected, BANDS)
    assert after < before * 0.3


@pytest.mark.parametrize(
    "method",
    [
        lambda s: baseline_polynomial(s, degree=3),
        lambda s: baseline_als(s, lam=1e6, p=0.01),
        lambda s: baseline_rubberband(s),
    ],
)
def test_baseline_methods_handle_transmittance_dips(method):
    # Noise is added in absorbance units before converting to %T, where its
    # effect is exponentially amplified -- keep it modest so the baseline
    # envelope isn't dominated by noise spikes rather than the real dips.
    s = make_transmittance_spectrum(bands=BANDS, baseline_slope=0.0, baseline_curve=0.0, noise=0.002)
    corrected = method(s)
    # baseline (away from dips) should now sit close to 100 %T
    mask = np.ones_like(s.x, dtype=bool)
    for center, _h, width in BANDS:
        mask &= np.abs(s.x - center) > width * 2
    assert np.mean(corrected.y[mask]) > 95.0


def test_baseline_polynomial_with_explicit_regions():
    s = make_absorbance_spectrum(bands=BANDS, baseline_slope=0.0008, noise=0.0005)
    regions = [(400.0, 900.0), (1900.0, 2700.0), (3200.0, 4000.0)]
    corrected, baseline = baseline_polynomial(s, degree=1, regions=regions, return_baseline=True)
    assert corrected.y.shape == s.y.shape
    assert baseline.y.shape == s.y.shape
    # regions mostly free of peaks -> baseline should track original data there
    mask = np.zeros_like(s.x, dtype=bool)
    for lo, hi in regions:
        mask |= (s.x >= lo) & (s.x <= hi)
    np.testing.assert_allclose(baseline.y[mask], s.y[mask], atol=0.05)


def test_baseline_polynomial_no_points_in_region_raises():
    s = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        baseline_polynomial(s, regions=[(10000.0, 20000.0)])


def test_return_baseline_tuple_shape():
    s = make_absorbance_spectrum()
    corrected, baseline = baseline_als(s, return_baseline=True)
    assert corrected.y.shape == baseline.y.shape == s.y.shape


def test_does_not_mutate_input():
    s = make_absorbance_spectrum(bands=BANDS)
    y_before = s.y.copy()
    baseline_polynomial(s)
    baseline_als(s)
    baseline_rubberband(s)
    np.testing.assert_array_equal(s.y, y_before)
