import numpy as np
import pytest

from openftir.processing import normalize

from test_processing_synth import make_absorbance_spectrum


def test_normalize_max():
    s = make_absorbance_spectrum(noise=0.0)
    n = normalize(s, method="max")
    assert np.isclose(np.max(np.abs(n.y)), 1.0)


def test_normalize_minmax():
    s = make_absorbance_spectrum(noise=0.0)
    n = normalize(s, method="minmax")
    assert np.isclose(np.min(n.y), 0.0, atol=1e-9)
    assert np.isclose(np.max(n.y), 1.0, atol=1e-9)


def test_normalize_area():
    s = make_absorbance_spectrum(noise=0.0)
    n = normalize(s, method="area")
    x_asc = n.x[np.argsort(n.x)]
    y_asc = n.y[np.argsort(n.x)]
    area = np.trapezoid(y_asc, x_asc)
    assert np.isclose(abs(area), 1.0, rtol=1e-6)


def test_normalize_vector():
    s = make_absorbance_spectrum(noise=0.0)
    n = normalize(s, method="vector")
    assert np.isclose(np.linalg.norm(n.y), 1.0)


def test_normalize_peak_at_position():
    s = make_absorbance_spectrum(bands=[(1050.0, 0.8, 20.0)], noise=0.0)
    n = normalize(s, method="peak", at=1050.0)
    idx = np.argmin(np.abs(n.x - 1050.0))
    assert np.isclose(n.y[idx], 1.0, atol=0.01)


def test_normalize_peak_requires_at():
    s = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        normalize(s, method="peak")


def test_normalize_unknown_method_raises():
    s = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        normalize(s, method="bogus")


def test_does_not_mutate_input():
    s = make_absorbance_spectrum()
    y_before = s.y.copy()
    normalize(s, method="max")
    np.testing.assert_array_equal(s.y, y_before)
