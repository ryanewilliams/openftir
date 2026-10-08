import numpy as np
import pytest

from openftir.spectrum import Spectrum, XUnit, YUnit
from openftir.processing import crop, interpolate_to, subtract, add, scale, offset, average, auto_subtract_factor

from test_processing_synth import make_absorbance_spectrum


def test_crop_keeps_range():
    s = make_absorbance_spectrum()
    c = crop(s, 1000.0, 2000.0)
    assert np.all((c.x >= 1000.0) & (c.x <= 2000.0))
    assert len(c) < len(s)


def test_crop_empty_raises():
    s = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        crop(s, 10000.0, 20000.0)


def test_interpolate_to_arbitrary_grid():
    s = make_absorbance_spectrum(noise=0.0)
    new_x = np.linspace(500.0, 3900.0, 500)
    r = interpolate_to(s, new_x)
    np.testing.assert_array_equal(r.x, new_x)
    assert r.y.shape == new_x.shape


def test_subtract_identical_gives_zero():
    s = make_absorbance_spectrum(noise=0.0)
    r = subtract(s, s)
    np.testing.assert_allclose(r.y, 0.0, atol=1e-9)


def test_add_and_scale_offset():
    s = make_absorbance_spectrum(noise=0.0)
    doubled = add(s, s)
    np.testing.assert_allclose(doubled.y, s.y * 2, atol=1e-9)
    scaled = scale(s, 2.0)
    np.testing.assert_allclose(scaled.y, s.y * 2)
    offsetted = offset(s, 1.5)
    np.testing.assert_allclose(offsetted.y, s.y + 1.5)


def test_subtract_with_factor_and_mismatched_grid():
    s = make_absorbance_spectrum(noise=0.0)
    coarse_x = np.linspace(s.x.min(), s.x.max(), 1200)
    coarse_y = np.interp(coarse_x, s.x[::-1], s.y[::-1]) * 0.5
    coarse = Spectrum(x=coarse_x, y=coarse_y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE)
    r = subtract(s, coarse, factor=2.0)
    # a coarser grid introduces some interpolation error under sharp peaks
    np.testing.assert_allclose(r.y, 0.0, atol=0.05)


def test_average_of_noisy_replicates_reduces_noise():
    reps = [make_absorbance_spectrum(noise=0.05, seed=i) for i in range(20)]
    avg = average(reps)
    clean = make_absorbance_spectrum(noise=0.0)
    err = np.std(avg.y - clean.y)
    single_err = np.std(reps[0].y - clean.y)
    assert err < single_err * 0.5


def test_auto_subtract_factor_recovers_known_scale():
    base = make_absorbance_spectrum(noise=0.0, baseline_slope=0.0, bands=[(2350.0, 0.3, 10.0)])  # mimic CO2-like band
    sample = make_absorbance_spectrum(noise=0.0, baseline_slope=0.0)
    contaminated = add(sample, base, factor=0.7)
    factor = auto_subtract_factor(contaminated, base, region=(2300.0, 2400.0))
    assert np.isclose(factor, 0.7, atol=0.05)
    cleaned = subtract(contaminated, base, factor=factor)
    np.testing.assert_allclose(cleaned.y, sample.y, atol=0.02)


def test_does_not_mutate_inputs():
    s = make_absorbance_spectrum()
    y_before = s.y.copy()
    subtract(s, s)
    add(s, s)
    scale(s, 2.0)
    offset(s, 1.0)
    crop(s, 500, 3000)
    np.testing.assert_array_equal(s.y, y_before)
