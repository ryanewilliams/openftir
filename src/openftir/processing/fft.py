"""Interferogram <-> single-beam spectrum FFT processing.

This is the core of the real instrument driver's pipeline: raw co-added
interferograms come in, phase-corrected single-beam spectra go out.

Conventions
-----------
Interferograms are indexed by sample number; the "optical retardation" (the
moving-mirror path difference) advances by ``dx = sampling_interval /
laser_wavenumber`` centimetres per sample, where ``sampling_interval`` is
the number of HeNe laser fringes between digitizer samples (the
undersampling ratio -- 1 for a fully-sampled interferogram).

Internally, arrays are worked on after *rolling* the ZPD (zero path
difference / centre-burst) sample to index 0 (the standard "ifftshift"
layout real-FFT code expects): index 0 is the centre burst, indices
``1..Np-1`` step through increasing positive retardation, and the last
``Nn`` samples hold the negative-retardation side (oldest sample first).
"""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

__all__ = [
    "apodize",
    "asymmetric_mertz_single_beam",
    "interferogram_to_single_beam",
    "single_beam_to_interferogram",
    "nominal_resolution_points",
]

_RESOLUTION_BROADENING = {
    "boxcar": 1.0,
    "triangular": 1.5,
    "happ-genzel": 1.3,
    "blackman-harris-3": 1.7,
    "blackman-harris-4": 1.9,
    "norton-beer-weak": 1.2,
    "norton-beer-medium": 1.4,
    "norton-beer-strong": 1.6,
    "cosine": 1.3,
}

_NORTON_BEER_COEFFS = {
    # C0, C1, C2, C3 for D(x) = sum Ci * (1-x^2)**i, normalised so sum(Ci) == 1.
    # Norton & Beer (1976) as tabulated by Naylor & Tahic, JOSA A 24 (2007).
    "norton-beer-weak": (0.384093, -0.087577, 0.703484, 0.000000),
    "norton-beer-medium": (0.152442, -0.136176, 0.983734, 0.000000),
    "norton-beer-strong": (0.045335, 0.000000, 0.554883, 0.399782),
}


def _window_shape(x: np.ndarray, kind: str) -> np.ndarray:
    """Evaluate an apodization window's shape function at normalised positions.

    ``x`` holds normalised positions in ``[-1, 1]`` (0 = ZPD/centre, +-1 =
    the edge of the (possibly asymmetric) interferogram).
    """
    x = np.clip(x, -1.0, 1.0)
    if kind == "boxcar":
        return np.ones_like(x)
    if kind == "triangular":
        return 1.0 - np.abs(x)
    if kind == "happ-genzel":
        return 0.54 + 0.46 * np.cos(np.pi * x)
    if kind == "cosine":
        return np.cos(np.pi * x / 2.0)
    if kind in ("blackman-harris-3", "blackman-harris-4"):
        t = (x + 1.0) / 2.0  # map to [0, 1]
        if kind == "blackman-harris-3":
            a0, a1, a2 = 0.42323, 0.49755, 0.07922
            return a0 - a1 * np.cos(2 * np.pi * t) + a2 * np.cos(4 * np.pi * t)
        a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
        return (
            a0
            - a1 * np.cos(2 * np.pi * t)
            + a2 * np.cos(4 * np.pi * t)
            - a3 * np.cos(6 * np.pi * t)
        )
    if kind in _NORTON_BEER_COEFFS:
        c0, c1, c2, c3 = _NORTON_BEER_COEFFS[kind]
        total = c0 + c1 + c2 + c3
        u = 1.0 - x**2
        val = c0 + c1 * u + c2 * u**2 + c3 * u**3
        return val / total
    raise ValueError(f"unknown apodization kind: {kind!r}")


def apodize(n: int, kind: str) -> np.ndarray:
    """Symmetric apodization window of length ``n``, centred at ``n // 2``.

    Supported kinds: "boxcar", "triangular", "happ-genzel",
    "blackman-harris-3", "blackman-harris-4", "norton-beer-weak/medium/strong",
    "cosine".
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    x = np.linspace(-1.0, 1.0, n)
    return _window_shape(x, kind)


def _asymmetric_window(n: int, Np: int, Nn: int, kind: str) -> np.ndarray:
    """Window for a ZPD-rolled array: positive side length ``Np`` (incl. ZPD
    at index 0), negative side length ``Nn`` at the tail, each side reaching
    the window's edge value at its own extent."""
    w = np.empty(n)
    if Np > 1:
        w[:Np] = _window_shape(np.arange(Np) / (Np - 1), kind)
    else:
        w[:Np] = 1.0
    if Nn > 0:
        k = np.arange(Nn)
        x_neg = (Nn - k) / Nn
        w[Np:] = _window_shape(x_neg, kind)
    return w


def nominal_resolution_points(resolution_cm: float, laser_wavenumber: float = 15798.0) -> int:
    """One-sided interferogram length (samples) needed for a given resolution.

    Nominal (unapodized, boxcar) spectral resolution is ``1/(N*dx)`` with
    ``dx = 1/laser_wavenumber`` (sampling_interval=1), so ``N =
    laser_wavenumber / resolution_cm``.
    """
    if resolution_cm <= 0:
        raise ValueError("resolution_cm must be positive")
    return max(int(round(laser_wavenumber / resolution_cm)), 2)


def _detect_zpd(igram: np.ndarray) -> int:
    return int(np.argmax(np.abs(igram - np.median(igram))))


def interferogram_to_single_beam(
    igram: np.ndarray,
    laser_wavenumber: float = 15798.0,
    sampling_interval: int = 1,
    apodization: str = "happ-genzel",
    zero_fill: int = 2,
    phase_correction: str = "mertz",
    phase_points: int = 256,
    x_min: float = 400.0,
    x_max: float = 4000.0,
    double_sided: bool | None = None,
) -> Spectrum:
    """FFT a raw (co-added) interferogram into a phase-corrected single-beam spectrum.

    ``phase_correction`` is one of "mertz", "power" (magnitude spectrum,
    phase-insensitive) or "none" (real part of the raw FFT, no correction).
    """
    igram = np.asarray(igram, dtype=float)
    n = igram.size
    if n < 8:
        raise ValueError("interferogram too short")

    dx = sampling_interval / laser_wavenumber  # cm per sample

    zpd = _detect_zpd(igram)
    Np = n - zpd  # positive side length, including the ZPD sample itself
    Nn = zpd  # negative side length

    if double_sided is None:
        double_sided = min(Np, Nn) >= max(phase_points, 8)

    rolled = np.roll(igram, -zpd)

    window = _asymmetric_window(n, Np, Nn, apodization)
    windowed = rolled * window

    zero_fill = max(int(zero_fill), 1)
    total_len = n * zero_fill
    padded = np.zeros(total_len)
    padded[:Np] = windowed[:Np]
    if Nn > 0:
        padded[-Nn:] = windowed[Np:]

    spectrum_complex = np.fft.rfft(padded)
    wavenumber = np.fft.rfftfreq(total_len, d=dx)

    if phase_correction == "none":
        sb = spectrum_complex.real
    elif phase_correction == "power":
        sb = np.abs(spectrum_complex)
    elif phase_correction == "mertz":
        short = max(min(phase_points, Np, Nn), 4) if min(Np, Nn) >= 4 else min(Np, Nn)
        if short < 4:
            # Not enough double-sided data to estimate a phase at all;
            # fall back to the phase-insensitive magnitude spectrum.
            sb = np.abs(spectrum_complex)
        else:
            low_window = _asymmetric_window(2 * short, short, short, apodization)
            low_arr = np.empty(2 * short)
            low_arr[:short] = rolled[:short]
            low_arr[short:] = rolled[-short:]
            low_arr *= low_window

            low_zero_fill = 4
            low_total = 2 * short * low_zero_fill
            low_padded = np.zeros(low_total)
            low_padded[:short] = low_arr[:short]
            low_padded[-short:] = low_arr[short:]

            low_complex = np.fft.rfft(low_padded)
            low_freq = np.fft.rfftfreq(low_total, d=dx)
            phase_low = np.unwrap(np.arctan2(low_complex.imag, low_complex.real))
            phase_high = np.interp(wavenumber, low_freq, phase_low)

            sb = spectrum_complex.real * np.cos(phase_high) + spectrum_complex.imag * np.sin(phase_high)
    else:
        raise ValueError(f"unknown phase_correction: {phase_correction!r}")

    mask = (wavenumber >= x_min) & (wavenumber <= x_max)
    if not np.any(mask):
        raise ValueError(f"no FFT bins fall inside [{x_min}, {x_max}] cm-1")
    x_out = wavenumber[mask][::-1]
    y_out = sb[mask][::-1]

    base_resolution = 1.0 / (max(Np, Nn) * dx)
    resolution_est = base_resolution * _RESOLUTION_BROADENING.get(apodization, 1.5)

    meta = {
        "resolution_cm": resolution_est,
        "apodization": apodization,
        "zpd_index": zpd,
        "double_sided": bool(double_sided),
        "zero_fill": zero_fill,
        "laser_wavenumber": laser_wavenumber,
        "sampling_interval": sampling_interval,
        "phase_correction": phase_correction,
    }

    return Spectrum(
        x=x_out,
        y=y_out,
        x_unit=XUnit.WAVENUMBER,
        y_unit=YUnit.SINGLE_BEAM,
        meta=meta,
        history=[f"interferogram -> single beam (apodization={apodization}, phase={phase_correction})"],
    )


def asymmetric_mertz_single_beam(
    igram: np.ndarray,
    zpd: int,
    laser_wavenumber: float,
    apodization: str = "norton-beer-medium",
    zero_fill: int = 2,
    x_min: float = 400.0,
    x_max: float = 4000.0,
) -> Spectrum:
    """Single beam from an asymmetric interferogram (short side before ZPD),
    Mertz phase correction with ramp weighting.

    For benches that record only ``zpd`` samples before the centre burst and
    many more after it (the Varian 3100). The double-sided region is counted
    twice by a plain FFT, so it is weighted by a linear ramp (0 at the start
    of the short side, 0.5 at ZPD, 1 at the mirror image of the start). The
    apodization runs over the long side; the phase comes from the
    double-sided region with a triangular window. The transform length is the
    next power of two at or above the interferogram length, times
    ``zero_fill``. Checked against Varian Resolutions Pro's own spectra.
    """
    igram = np.asarray(igram, dtype=float)
    n = igram.size
    if not 4 <= zpd < n - 4:
        raise ValueError(f"ZPD index {zpd} outside the interferogram (length {n})")
    dx = 1.0 / laser_wavenumber
    j = np.arange(n) - zpd
    long_side = n - 1 - zpd

    ramp = np.clip((j + zpd) / (2.0 * zpd), 0.0, 1.0)
    weighted = igram * ramp * _window_shape(np.abs(j) / long_side, apodization)

    size = (1 << (n - 1).bit_length()) * max(int(zero_fill), 1)
    buf = np.zeros(size)
    buf[: n - zpd] = weighted[zpd:]
    buf[size - zpd :] = weighted[:zpd]
    full = np.fft.rfft(buf)

    short = np.abs(j) <= zpd
    phase_buf = np.zeros(size)
    js = j[short]
    seg = igram[short] * (1.0 - np.abs(js) / zpd)
    phase_buf[js[js >= 0]] = seg[js >= 0]
    phase_buf[size + js[js < 0]] = seg[js < 0]
    low = np.fft.rfft(phase_buf)
    phase = np.arctan2(low.imag, low.real)
    sb = full.real * np.cos(phase) + full.imag * np.sin(phase)

    wavenumber = np.fft.rfftfreq(size, d=dx)
    mask = (wavenumber >= x_min) & (wavenumber <= x_max)
    if not np.any(mask):
        raise ValueError(f"no FFT bins fall inside [{x_min}, {x_max}] cm-1")
    resolution = 1.0 / (long_side * dx) * _RESOLUTION_BROADENING.get(apodization, 1.5)
    return Spectrum(
        x=wavenumber[mask][::-1],
        y=sb[mask][::-1],
        x_unit=XUnit.WAVENUMBER,
        y_unit=YUnit.SINGLE_BEAM,
        meta={
            "resolution_cm": resolution,
            "apodization": apodization,
            "zpd_index": zpd,
            "double_sided": False,
            "zero_fill": zero_fill,
            "fft_size": size,
            "laser_wavenumber": laser_wavenumber,
            "phase_correction": "mertz (ramp)",
        },
        history=[f"interferogram -> single beam (asymmetric Mertz, apodization={apodization})"],
    )


def single_beam_to_interferogram(sb_spectrum: Spectrum, n_points: int, laser_wavenumber: float = 15798.0) -> Spectrum:
    """Synthesise a double-sided, ZPD-centred interferogram from a single-beam spectrum.

    Used by the simulated instrument driver and by round-trip tests. Zero
    phase is assumed (the spectrum's own x axis supplies the magnitude only);
    signal outside the spectrum's x range is treated as zero.
    """
    if n_points < 8:
        raise ValueError("n_points too small")
    order = np.argsort(sb_spectrum.x)
    x_asc, y_asc = sb_spectrum.x[order], sb_spectrum.y[order]

    dx = 1.0 / laser_wavenumber  # sampling_interval = 1
    freq_full = np.fft.rfftfreq(n_points, d=dx)

    y_on_grid = np.interp(freq_full, x_asc, y_asc, left=0.0, right=0.0)
    igram_half = np.fft.irfft(y_on_grid.astype(complex), n=n_points)
    igram = np.fft.fftshift(igram_half)
    zpd_index = n_points // 2

    return Spectrum(
        x=np.arange(n_points, dtype=float),
        y=igram,
        x_unit=XUnit.POINTS,
        y_unit=YUnit.INTERFEROGRAM,
        meta={"zpd_index": zpd_index, "laser_wavenumber": laser_wavenumber, "dx_cm": dx},
        history=["single beam -> synthetic interferogram"],
    )
