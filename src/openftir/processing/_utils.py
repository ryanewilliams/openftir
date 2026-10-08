"""Internal helpers shared across ``openftir.processing`` submodules.

Not part of the public API (not re-exported from ``openftir.processing``).
"""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum, YUnit


def sort_ascending(x: np.ndarray, y: np.ndarray):
    """Return ``(x_asc, y_asc, order)`` with ``x_asc = x[order]`` ascending."""
    order = np.argsort(x, kind="stable")
    return x[order], y[order], order


def restore_order(values_ascending: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Undo :func:`sort_ascending`: scatter ``values_ascending`` back to original order."""
    out = np.empty_like(values_ascending)
    out[order] = values_ascending
    return out


def interp_values(x_target: np.ndarray, x_src: np.ndarray, y_src: np.ndarray) -> np.ndarray:
    """Linearly interpolate ``y_src(x_src)`` at ``x_target``.

    Handles ``x_src`` given in either ascending or descending order. Points of
    ``x_target`` outside the range of ``x_src`` are clamped to the nearest
    edge value (flat extrapolation, matching :func:`numpy.interp`).
    """
    x_target = np.asarray(x_target, dtype=float)
    xs, ys, _ = sort_ascending(np.asarray(x_src, dtype=float), np.asarray(y_src, dtype=float))
    return np.interp(x_target, xs, ys)


def resolve_peaks_down(y_unit: YUnit, peaks: str) -> bool:
    """Decide whether spectral features point "down" from the baseline.

    ``peaks="auto"`` treats %T / %R spectra (features dip toward 0) as
    "down" and everything else (absorbance, Kubelka-Munk, single beam, ...)
    as "up".
    """
    if peaks == "auto":
        return y_unit in (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE)
    if peaks == "down":
        return True
    if peaks == "up":
        return False
    raise ValueError(f"peaks must be 'auto', 'up' or 'down', got {peaks!r}")


def baseline_reference_level(y_unit: YUnit, peaks_down: bool) -> float:
    """Reference level that a baseline-corrected spectrum should sit on.

    For %T / %R data with downward-pointing features we re-anchor the
    baseline to 100 (no absorption) rather than 0, so peaks keep their
    natural depth below the 100 % line.
    """
    if peaks_down and y_unit in (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE):
        return 100.0
    return 0.0


def median_spacing(x: np.ndarray) -> float:
    """Median absolute sample spacing of ``x`` (handles descending axes)."""
    return float(np.median(np.abs(np.diff(x))))
