"""Peak finding and peak-area measurement."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks as _scipy_find_peaks
from scipy.signal import peak_widths as _scipy_peak_widths

from openftir.spectrum import Spectrum

from ._utils import median_spacing, resolve_peaks_down

__all__ = ["Peak", "find_peaks", "peak_area"]


@dataclass
class Peak:
    position: float
    height: float
    width: float | None = None
    area: float | None = None


def find_peaks(
    s: Spectrum,
    prominence: float | None = None,
    min_distance_cm: float | None = None,
    threshold: float | None = None,
    direction: str = "auto",
) -> list[Peak]:
    """Find peaks, returned as a list of :class:`Peak` sorted by position.

    ``direction`` ("auto"|"up"|"down") picks whether features point up
    (absorbance-like) or down (%T/%R dips); "auto" decides from
    ``s.y_unit``. ``threshold`` is a minimum peak height (passed to
    ``scipy.signal.find_peaks`` as its ``height`` argument -- named
    ``threshold`` here to match the spectroscopy sense of a cutoff level,
    which differs from scipy's own ``threshold`` parameter).

    With ``prominence=None`` a default of 2 % of the spectrum's y range is
    used so that noise is not reported as peaks; pass ``0`` to get every
    local extremum.
    """
    down = resolve_peaks_down(s.y_unit, direction)
    y_search = -s.y if down else s.y

    if prominence is None:
        finite = y_search[np.isfinite(y_search)]
        prominence = 0.02 * float(np.ptp(finite)) if finite.size else None

    distance = None
    if min_distance_cm is not None:
        spacing = median_spacing(s.x)
        distance = max(int(round(min_distance_cm / spacing)), 1)

    indices, _props = _scipy_find_peaks(
        y_search, prominence=prominence, distance=distance, height=threshold
    )

    peaks: list[Peak] = []
    if len(indices) > 0:
        widths_samples, *_ = _scipy_peak_widths(y_search, indices, rel_height=0.5)
        spacing = median_spacing(s.x)
        for idx, w_samples in zip(indices, widths_samples):
            peaks.append(
                Peak(
                    position=float(s.x[idx]),
                    height=float(s.y[idx]),
                    width=float(w_samples * spacing),
                )
            )

    peaks.sort(key=lambda p: p.position)
    return peaks


def peak_area(s: Spectrum, x1: float, x2: float, baseline: str = "linear") -> float:
    """Integrate the area of a peak between ``x1`` and ``x2``.

    ``baseline="linear"`` subtracts the straight line connecting the
    spectrum's values at ``x1`` and ``x2`` before integrating (trapezoidal
    rule). Returns a plain float.
    """
    if baseline != "linear":
        raise ValueError("only baseline='linear' is currently supported")

    lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
    order = np.argsort(s.x)
    x_asc, y_asc = s.x[order], s.y[order]
    mask = (x_asc >= lo) & (x_asc <= hi)
    if np.count_nonzero(mask) < 2:
        raise ValueError(f"region [{x1}, {x2}] contains fewer than 2 data points")

    xr, yr = x_asc[mask], y_asc[mask]
    line = np.interp(xr, [xr[0], xr[-1]], [yr[0], yr[-1]])
    return float(np.trapezoid(yr - line, xr))
