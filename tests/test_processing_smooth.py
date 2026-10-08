import numpy as np
import pytest

from openftir.processing import savgol, derivative, moving_average

from test_processing_synth import make_absorbance_spectrum


@pytest.mark.parametrize("descending", [True, False])
def test_savgol_reduces_noise(descending):
    clean = make_absorbance_spectrum(noise=0.0, descending=descending)
    noisy = make_absorbance_spectrum(noise=0.05, descending=descending)
    smoothed = savgol(noisy, window=15, order=3)
    err_before = np.std(noisy.y - clean.y)
    err_after = np.std(smoothed.y - clean.y)
    assert err_after < err_before * 0.6


def test_moving_average_reduces_noise_and_preserves_length():
    noisy = make_absorbance_spectrum(noise=0.05)
    smoothed = moving_average(noisy, window=9)
    assert smoothed.y.shape == noisy.y.shape
    assert np.std(np.diff(smoothed.y)) < np.std(np.diff(noisy.y))


@pytest.mark.parametrize("descending", [True, False])
def test_first_derivative_sign_and_zero_crossing_at_peak(descending):
    s = make_absorbance_spectrum(
        bands=[(1500.0, 1.0, 40.0)], baseline_slope=0.0, noise=0.0, descending=descending, n=2001
    )
    d1 = derivative(s, order=1, window=25, polyorder=3)
    idx_peak = np.argmin(np.abs(s.x - 1500.0))
    # derivative should cross zero very near the peak center
    window = slice(max(idx_peak - 5, 0), idx_peak + 6)
    assert np.any(np.diff(np.sign(d1.y[window])) != 0) or abs(d1.y[idx_peak]) < 0.01


def test_second_derivative_is_negative_at_peak_top():
    s = make_absorbance_spectrum(bands=[(1500.0, 1.0, 40.0)], baseline_slope=0.0, noise=0.0, n=2001)
    d2 = derivative(s, order=2, window=25, polyorder=3)
    idx_peak = np.argmin(np.abs(s.x - 1500.0))
    assert d2.y[idx_peak] < 0


def test_derivative_sign_consistent_regardless_of_axis_direction():
    asc = make_absorbance_spectrum(bands=[(1500.0, 1.0, 40.0)], baseline_slope=0.0, noise=0.0, descending=False, n=2001)
    desc = make_absorbance_spectrum(bands=[(1500.0, 1.0, 40.0)], baseline_slope=0.0, noise=0.0, descending=True, n=2001)
    d1_asc = derivative(asc, order=1, window=25, polyorder=3)
    d1_desc = derivative(desc, order=1, window=25, polyorder=3)
    # resample the descending-axis result onto the ascending grid and compare sign/shape
    resampled = np.interp(asc.x, desc.x[::-1], d1_desc.y[::-1])
    corr = np.corrcoef(d1_asc.y, resampled)[0, 1]
    assert corr > 0.9


def test_does_not_mutate_input():
    s = make_absorbance_spectrum()
    y_before = s.y.copy()
    savgol(s)
    derivative(s)
    moving_average(s)
    np.testing.assert_array_equal(s.y, y_before)
