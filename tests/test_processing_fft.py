import numpy as np
import pytest

from openftir.spectrum import Spectrum, XUnit, YUnit
from openftir.processing import (
    apodize,
    interferogram_to_single_beam,
    single_beam_to_interferogram,
    nominal_resolution_points,
)

from test_processing_synth import lorentzian

LASER = 15798.0
BAND_CENTER = 1500.0


def _reference_sb(n=4001, x_min=400.0, x_max=4000.0):
    x = np.linspace(x_min, x_max, n)
    y = 1.0 - 0.6 * lorentzian(x, BAND_CENTER, 1.0, 25.0)
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM)


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a, b)[0, 1])


@pytest.mark.parametrize(
    "kind",
    [
        "boxcar",
        "triangular",
        "happ-genzel",
        "blackman-harris-3",
        "blackman-harris-4",
        "norton-beer-weak",
        "norton-beer-medium",
        "norton-beer-strong",
        "cosine",
    ],
)
def test_apodize_shapes(kind):
    w = apodize(101, kind)
    assert w.shape == (101,)
    assert np.isclose(w[50], 1.0, atol=0.02)  # centre is (near) full weight
    if kind != "boxcar":
        assert w[0] < w[50]
        assert w[-1] < w[50]
    assert np.all(np.isfinite(w))


def test_apodize_boxcar_is_flat():
    w = apodize(50, "boxcar")
    np.testing.assert_allclose(w, 1.0)


def test_nominal_resolution_points():
    pts = nominal_resolution_points(4.0, laser_wavenumber=LASER)
    assert pts == round(LASER / 4.0)
    assert nominal_resolution_points(1.0, laser_wavenumber=LASER) > nominal_resolution_points(8.0, laser_wavenumber=LASER)


def test_single_beam_to_interferogram_is_zpd_centered():
    sb = _reference_sb()
    igram = single_beam_to_interferogram(sb, n_points=4096, laser_wavenumber=LASER)
    assert igram.y_unit == YUnit.INTERFEROGRAM
    zpd = igram.meta["zpd_index"]
    assert zpd == 4096 // 2
    # the centre burst should be the (near) largest-magnitude sample
    assert np.argmax(np.abs(igram.y)) in range(zpd - 2, zpd + 3)


@pytest.mark.parametrize("apodization", ["happ-genzel", "triangular", "norton-beer-medium"])
def test_fft_round_trip_recovers_band_position_and_shape(apodization):
    sb = _reference_sb()
    n_points = nominal_resolution_points(2.0, laser_wavenumber=LASER) * 2
    igram = single_beam_to_interferogram(sb, n_points=n_points, laser_wavenumber=LASER)

    recovered = interferogram_to_single_beam(
        igram.y,
        laser_wavenumber=LASER,
        sampling_interval=1,
        apodization=apodization,
        zero_fill=2,
        phase_correction="mertz",
        phase_points=256,
        x_min=400.0,
        x_max=4000.0,
    )
    assert recovered.y_unit == YUnit.SINGLE_BEAM
    assert recovered.x_unit == XUnit.WAVENUMBER
    assert recovered.x[0] > recovered.x[-1]  # descending, FTIR convention

    ref_on_grid = np.interp(recovered.x[::-1], sb.x, sb.y)[::-1]
    corr = _correlation(recovered.y, ref_on_grid)
    assert corr > 0.97

    idx_min = int(np.argmin(recovered.y))
    resolution = recovered.meta["resolution_cm"]
    assert abs(recovered.x[idx_min] - BAND_CENTER) < max(resolution * 3, 10.0)


def test_fft_round_trip_asymmetric_interferogram_needs_mertz():
    """Build an interferogram with a deliberate (ZPD-miscentring-like) phase
    error and truncate it to a single-sided acquisition, then check that
    Mertz phase correction recovers the spectrum while "none" does not.
    """
    sb = _reference_sb()
    n_points = nominal_resolution_points(2.0, laser_wavenumber=LASER) * 2
    dx = 1.0 / LASER
    freq_full = np.fft.rfftfreq(n_points, d=dx)
    y_on_grid = np.interp(freq_full, sb.x, sb.y, left=0.0, right=0.0)

    # constant + mild linear phase ramp: mimics a sub-sample ZPD offset
    phase = 0.6 + 0.0004 * freq_full
    complex_spectrum = y_on_grid * np.exp(1j * phase)
    igram_half = np.fft.irfft(complex_spectrum, n=n_points)
    igram = np.fft.fftshift(igram_half)
    zpd = int(np.argmax(np.abs(igram)))

    # Simulate a single-sided acquisition: only a short double-sided
    # centre-burst survives, the rest of the array is single-sided.
    short = 400
    truncated = igram[zpd - short :]

    recovered_mertz = interferogram_to_single_beam(
        truncated,
        laser_wavenumber=LASER,
        apodization="happ-genzel",
        zero_fill=2,
        phase_correction="mertz",
        phase_points=256,
        x_min=400.0,
        x_max=4000.0,
    )
    recovered_none = interferogram_to_single_beam(
        truncated,
        laser_wavenumber=LASER,
        apodization="happ-genzel",
        zero_fill=2,
        phase_correction="none",
        x_min=400.0,
        x_max=4000.0,
    )

    ref_mertz = np.interp(recovered_mertz.x[::-1], sb.x, sb.y)[::-1]
    ref_none = np.interp(recovered_none.x[::-1], sb.x, sb.y)[::-1]
    corr_mertz = _correlation(recovered_mertz.y, ref_mertz)
    corr_none = _correlation(recovered_none.y, ref_none)

    assert corr_mertz > 0.9
    assert corr_mertz > corr_none + 0.3


def test_power_phase_correction_is_phase_insensitive_magnitude():
    sb = _reference_sb()
    n_points = nominal_resolution_points(4.0, laser_wavenumber=LASER) * 2
    igram = single_beam_to_interferogram(sb, n_points=n_points, laser_wavenumber=LASER)
    recovered = interferogram_to_single_beam(
        igram.y, laser_wavenumber=LASER, apodization="happ-genzel", zero_fill=2,
        phase_correction="power", x_min=400.0, x_max=4000.0,
    )
    assert np.all(recovered.y >= 0.0)


def test_interferogram_to_single_beam_meta_fields():
    sb = _reference_sb()
    n_points = nominal_resolution_points(4.0, laser_wavenumber=LASER) * 2
    igram = single_beam_to_interferogram(sb, n_points=n_points, laser_wavenumber=LASER)
    recovered = interferogram_to_single_beam(igram.y, laser_wavenumber=LASER)
    for key in ("resolution_cm", "apodization", "zpd_index", "double_sided", "zero_fill"):
        assert key in recovered.meta
