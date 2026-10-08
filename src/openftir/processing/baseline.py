"""Baseline estimation and correction.

All three methods accept a ``peaks`` argument (``"auto"|"up"|"down"``):
features pointing "up" (absorbance-like: baseline runs under the peaks) or
"down" (%T/%R-like: baseline runs over the dips). ``"auto"`` decides from
``s.y_unit``. For %T/%R data the corrected spectrum is re-anchored to the
100 % line instead of 0, so dip depths are preserved.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import diags, spdiags
from scipy.sparse.linalg import spsolve

from openftir.spectrum import Spectrum

from ._utils import baseline_reference_level, resolve_peaks_down, sort_ascending, restore_order

__all__ = ["baseline_polynomial", "baseline_als", "baseline_rubberband"]


def _iterative_poly_envelope(x: np.ndarray, y: np.ndarray, degree: int, peaks_down: bool, n_iter: int = 8) -> np.ndarray:
    y_work = y.astype(float).copy()
    baseline = y_work
    for _ in range(n_iter):
        coeffs = np.polyfit(x, y_work, degree)
        baseline = np.polyval(coeffs, x)
        if peaks_down:
            y_work = np.maximum(y_work, baseline)
        else:
            y_work = np.minimum(y_work, baseline)
    return baseline


def baseline_polynomial(
    s: Spectrum,
    degree: int = 2,
    regions: list[tuple[float, float]] | None = None,
    return_baseline: bool = False,
    peaks: str = "auto",
):
    """Polynomial baseline correction.

    If ``regions`` (a list of ``(x1, x2)`` anchor ranges known to contain no
    peaks) is given, the polynomial is least-squares fit to the data inside
    those ranges only. Otherwise an iterative envelope fit (Lieber &
    Mahadevan-Jansen style) converges the polynomial onto the lower (or
    upper, for downward peaks) envelope of the whole spectrum.
    """
    x, y = s.x, s.y
    peaks_down = resolve_peaks_down(s.y_unit, peaks)

    if regions:
        mask = np.zeros(x.shape, dtype=bool)
        for x1, x2 in regions:
            lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
            mask |= (x >= lo) & (x <= hi)
        if not np.any(mask):
            raise ValueError("no data points fall inside the given baseline regions")
        coeffs = np.polyfit(x[mask], y[mask], degree)
        baseline = np.polyval(coeffs, x)
    else:
        baseline = _iterative_poly_envelope(x, y, degree, peaks_down)

    ref = baseline_reference_level(s.y_unit, peaks_down)
    corrected_y = y - baseline + ref
    corrected = s.derive(corrected_y, f"polynomial baseline correction (degree={degree})")
    if not return_baseline:
        return corrected
    baseline_spec = s.derive(baseline.copy(), f"polynomial baseline (degree={degree})")
    return corrected, baseline_spec


def _als(y: np.ndarray, lam: float, p: float, niter: int) -> np.ndarray:
    """Asymmetric Least Squares smoothing baseline (Eilers & Boelens, 2005)."""
    length = len(y)
    diff_mat = diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(length, length - 2))
    penalty = lam * diff_mat.dot(diff_mat.transpose())
    w = np.ones(length)
    z = y.copy()
    for _ in range(niter):
        weights = spdiags(w, 0, length, length)
        z = spsolve((weights + penalty).tocsc(), w * y)
        w = p * (y > z) + (1.0 - p) * (y < z)
    return z


def baseline_als(
    s: Spectrum,
    lam: float = 1e5,
    p: float = 0.01,
    niter: int = 10,
    return_baseline: bool = False,
    peaks: str = "auto",
):
    """Asymmetric Least Squares (ALS) baseline correction.

    ``lam`` controls baseline smoothness and ``p`` the asymmetry (small ``p``
    makes the baseline hug the lower envelope of upward-pointing features).
    Works directly on ``s.y`` in its given order, so descending (or
    ascending) x axes are both fine.
    """
    if len(s) < 4:
        raise ValueError("baseline_als needs at least 4 points")
    peaks_down = resolve_peaks_down(s.y_unit, peaks)
    sign = -1.0 if peaks_down else 1.0
    baseline = sign * _als(sign * s.y, lam, p, niter)

    ref = baseline_reference_level(s.y_unit, peaks_down)
    corrected_y = s.y - baseline + ref
    corrected = s.derive(corrected_y, f"ALS baseline correction (lam={lam:g}, p={p:g})")
    if not return_baseline:
        return corrected
    baseline_spec = s.derive(baseline.copy(), f"ALS baseline (lam={lam:g}, p={p:g})")
    return corrected, baseline_spec


def _lower_hull(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lower convex hull of ascending-x points, via the monotone chain algorithm."""
    hull_x: list[float] = []
    hull_y: list[float] = []
    for xi, yi in zip(x, y):
        while len(hull_x) >= 2:
            ox, oy = hull_x[-2], hull_y[-2]
            ax, ay = hull_x[-1], hull_y[-1]
            cross = (ax - ox) * (yi - oy) - (ay - oy) * (xi - ox)
            if cross <= 0:
                hull_x.pop()
                hull_y.pop()
            else:
                break
        hull_x.append(xi)
        hull_y.append(yi)
    return np.asarray(hull_x), np.asarray(hull_y)


def baseline_rubberband(s: Spectrum, return_baseline: bool = False, peaks: str = "auto"):
    """Rubberband (convex hull) baseline correction.

    Stretches a "rubber band" under (or over, for downward peaks) the
    spectrum: the baseline is the piecewise-linear convex hull touching the
    data from below/above.
    """
    peaks_down = resolve_peaks_down(s.y_unit, peaks)
    x_asc, y_asc, order = sort_ascending(s.x, s.y)

    hull_y_sign = -y_asc if peaks_down else y_asc
    hx, hy = _lower_hull(x_asc, hull_y_sign)
    baseline_asc = np.interp(x_asc, hx, hy)
    if peaks_down:
        baseline_asc = -baseline_asc

    baseline = restore_order(baseline_asc, order)
    ref = baseline_reference_level(s.y_unit, peaks_down)
    corrected_y = s.y - baseline + ref
    corrected = s.derive(corrected_y, "rubberband (convex hull) baseline correction")
    if not return_baseline:
        return corrected
    baseline_spec = s.derive(baseline.copy(), "rubberband (convex hull) baseline")
    return corrected, baseline_spec
