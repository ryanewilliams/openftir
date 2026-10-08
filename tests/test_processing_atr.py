import numpy as np
import pytest

from openftir.processing import atr_correction

from test_processing_synth import make_absorbance_spectrum


def test_atr_correction_unchanged_at_1000cm1():
    s = make_absorbance_spectrum(bands=[(1000.0, 0.5, 20.0)], baseline_slope=0.0, noise=0.0)
    corrected = atr_correction(s)
    idx = np.argmin(np.abs(s.x - 1000.0))
    assert np.isclose(corrected.y[idx], s.y[idx], rtol=1e-6)


def test_atr_correction_direction():
    s = make_absorbance_spectrum(bands=[(500.0, 0.5, 10.0), (3000.0, 0.5, 10.0)], baseline_slope=0.0, noise=0.0)
    corrected = atr_correction(s)
    idx_low = np.argmin(np.abs(s.x - 500.0))
    idx_high = np.argmin(np.abs(s.x - 3000.0))
    # low wavenumber band should be reduced, high wavenumber band boosted
    assert corrected.y[idx_low] < s.y[idx_low]
    assert corrected.y[idx_high] > s.y[idx_high]


def test_atr_correction_requires_absorbance():
    s = make_absorbance_spectrum()
    from openftir.processing import to_transmittance

    t = to_transmittance(s)
    with pytest.raises(ValueError):
        atr_correction(t)


def test_atr_correction_rejects_subcritical_angle():
    s = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        atr_correction(s, n_crystal=1.5, n_sample=2.4, angle_deg=45.0)


def test_does_not_mutate_input():
    s = make_absorbance_spectrum()
    y_before = s.y.copy()
    atr_correction(s)
    np.testing.assert_array_equal(s.y, y_before)
