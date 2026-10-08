"""Cropping, interpolation and basic arithmetic between spectra."""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum

from ._utils import interp_values, sort_ascending

__all__ = [
    "crop",
    "interpolate_to",
    "subtract",
    "add",
    "scale",
    "offset",
    "average",
    "auto_subtract_factor",
]


def crop(s: Spectrum, x_min: float, x_max: float) -> Spectrum:
    """Keep only points with ``x_min <= x <= x_max`` (works for either axis direction)."""
    lo, hi = (x_min, x_max) if x_min <= x_max else (x_max, x_min)
    mask = (s.x >= lo) & (s.x <= hi)
    if not np.any(mask):
        raise ValueError(f"crop range ({x_min}, {x_max}) contains no data points")
    return s.derive(s.y[mask], f"crop to [{lo}, {hi}]", x=s.x[mask])


def interpolate_to(s: Spectrum, x: np.ndarray) -> Spectrum:
    """Resample onto an arbitrary x grid (any order) via linear interpolation."""
    x = np.asarray(x, dtype=float)
    y = interp_values(x, s.x, s.y)
    return s.derive(y, f"interpolated onto {len(x)}-point grid", x=x)


def subtract(a: Spectrum, b: Spectrum, factor: float = 1.0) -> Spectrum:
    """``a - factor * b``, with ``b`` interpolated onto ``a``'s x axis."""
    b_y = interp_values(a.x, b.x, b.y)
    return a.derive(a.y - factor * b_y, f"subtract (factor={factor:g})")


def add(a: Spectrum, b: Spectrum, factor: float = 1.0) -> Spectrum:
    """``a + factor * b``, with ``b`` interpolated onto ``a``'s x axis."""
    b_y = interp_values(a.x, b.x, b.y)
    return a.derive(a.y + factor * b_y, f"add (factor={factor:g})")


def scale(s: Spectrum, k: float) -> Spectrum:
    """Multiply y values by ``k``."""
    return s.derive(s.y * k, f"scale (x{k:g})")


def offset(s: Spectrum, k: float) -> Spectrum:
    """Add a constant ``k`` to y values."""
    return s.derive(s.y + k, f"offset (+{k:g})")


def average(spectra: list[Spectrum]) -> Spectrum:
    """Average several spectra, interpolating each onto the first spectrum's x axis."""
    if not spectra:
        raise ValueError("average() needs at least one spectrum")
    ref = spectra[0]
    stacked = np.stack([interp_values(ref.x, s.x, s.y) for s in spectra], axis=0)
    y = stacked.mean(axis=0)
    return ref.derive(y, f"average of {len(spectra)} spectra")


def auto_subtract_factor(a: Spectrum, b: Spectrum, region: tuple[float, float] | None = None) -> float:
    """Least-squares factor ``f`` minimising ``||a - f*b||`` (e.g. water vapour/CO2 removal).

    Returns a plain float (not a Spectrum) -- feed it to :func:`subtract`.
    """
    b_y_full = interp_values(a.x, b.x, b.y)
    if region is None:
        a_vals, b_vals = a.y, b_y_full
    else:
        lo, hi = (region[0], region[1]) if region[0] <= region[1] else (region[1], region[0])
        mask = (a.x >= lo) & (a.x <= hi)
        if not np.any(mask):
            raise ValueError(f"region {region} contains no data points")
        a_vals, b_vals = a.y[mask], b_y_full[mask]

    denom = float(np.dot(b_vals, b_vals))
    if denom == 0:
        raise ValueError("auto_subtract_factor: reference spectrum is zero in the given region")
    return float(np.dot(a_vals, b_vals) / denom)
