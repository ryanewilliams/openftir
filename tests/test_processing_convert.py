import numpy as np
import pytest

from openftir.spectrum import Spectrum, XUnit, YUnit
from openftir.processing import ratio, to_absorbance, to_transmittance, kubelka_munk, convert_x

from test_processing_synth import make_absorbance_spectrum, make_transmittance_spectrum


def _single_beam(descending=True, seed=0):
    s = make_absorbance_spectrum(descending=descending, seed=seed, noise=0.0)
    sb_y = np.power(10.0, -s.y) * 1000.0  # arbitrary intensity units
    return s.derive(sb_y, "-> single beam", y_unit=YUnit.SINGLE_BEAM)


@pytest.mark.parametrize("descending", [True, False])
def test_ratio_identical_spectra_gives_100_percent_t(descending):
    sb = _single_beam(descending=descending)
    result = ratio(sb, sb, to=YUnit.TRANSMITTANCE)
    assert result.y_unit == YUnit.TRANSMITTANCE
    np.testing.assert_allclose(result.y, 100.0, atol=1e-6)


def test_ratio_to_absorbance_of_identical_spectra_is_zero():
    sb = _single_beam()
    result = ratio(sb, sb, to=YUnit.ABSORBANCE)
    assert result.y_unit == YUnit.ABSORBANCE
    np.testing.assert_allclose(result.y, 0.0, atol=1e-6)


def test_ratio_interpolates_mismatched_axes():
    sample = _single_beam()
    # background on a differently-spaced (but still dense) grid
    bg_x = np.linspace(sample.x.min(), sample.x.max(), 1600)
    bg_y = np.interp(bg_x, sample.x[::-1], sample.y[::-1])
    background = Spectrum(x=bg_x, y=bg_y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM)
    result = ratio(sample, background, to=YUnit.TRANSMITTANCE)
    assert result.x.shape == sample.x.shape
    # some interpolation error is expected under the sharpest peak curvature
    np.testing.assert_allclose(result.y, 100.0, atol=5.0)


def test_absorbance_transmittance_round_trip():
    s = make_absorbance_spectrum(noise=0.0)
    t = to_transmittance(s)
    assert t.y_unit == YUnit.TRANSMITTANCE
    back = to_absorbance(t)
    np.testing.assert_allclose(back.y, s.y, atol=1e-9)


def test_to_absorbance_passthrough_and_errors():
    s = make_absorbance_spectrum(noise=0.0)
    same = to_absorbance(s)
    np.testing.assert_allclose(same.y, s.y)
    sb = _single_beam()
    with pytest.raises(ValueError):
        to_absorbance(sb)


def test_kubelka_munk_requires_reflectance():
    s = make_absorbance_spectrum(noise=0.0)
    with pytest.raises(ValueError):
        kubelka_munk(s)
    refl = s.derive(np.clip(s.y, 0, 1) * 50 + 10, "fake %R", y_unit=YUnit.REFLECTANCE)
    km = kubelka_munk(refl)
    assert km.y_unit == YUnit.KUBELKA_MUNK
    r = refl.y / 100.0
    expected = (1 - r) ** 2 / (2 * r)
    np.testing.assert_allclose(km.y, expected)


@pytest.mark.parametrize(
    "to_unit",
    [XUnit.WAVELENGTH_UM, XUnit.WAVELENGTH_NM],
)
def test_convert_x_round_trip(to_unit):
    s = make_absorbance_spectrum(noise=0.0)
    converted = convert_x(s, to_unit)
    assert converted.x_unit == to_unit
    np.testing.assert_allclose(converted.y, s.y)
    back = convert_x(converted, XUnit.WAVENUMBER)
    np.testing.assert_allclose(back.x, s.x, rtol=1e-9)


def test_convert_x_known_value():
    s = Spectrum(x=np.array([1000.0, 2000.0]), y=np.array([1.0, 2.0]), x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE)
    um = convert_x(s, XUnit.WAVELENGTH_UM)
    np.testing.assert_allclose(um.x, [10.0, 5.0])
    nm = convert_x(s, XUnit.WAVELENGTH_NM)
    np.testing.assert_allclose(nm.x, [10000.0, 5000.0])


def test_convert_x_points_rejected():
    s = Spectrum(x=np.arange(10.0), y=np.arange(10.0), x_unit=XUnit.POINTS, y_unit=YUnit.INTERFEROGRAM)
    with pytest.raises(ValueError):
        convert_x(s, XUnit.WAVENUMBER)


def test_does_not_mutate_input():
    s = make_absorbance_spectrum(noise=0.0)
    x_before = s.x.copy()
    y_before = s.y.copy()
    to_transmittance(s)
    convert_x(s, XUnit.WAVELENGTH_UM)
    np.testing.assert_array_equal(s.x, x_before)
    np.testing.assert_array_equal(s.y, y_before)
