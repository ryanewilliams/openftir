"""Spectrum normalization."""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum

from ._utils import interp_values, sort_ascending

__all__ = ["normalize"]


def normalize(s: Spectrum, method: str = "max", at: float | None = None) -> Spectrum:
    """Normalize a spectrum's y values.

    Methods:
      - ``"max"``: divide by the largest absolute value (result max |y| = 1).
      - ``"minmax"``: rescale to the 0-1 range.
      - ``"area"``: divide by the (absolute) integral area under the curve.
      - ``"vector"``: divide by the L2 (Euclidean) norm.
      - ``"peak"``: divide so the value at x=``at`` equals 1 (requires ``at``).
    """
    y = s.y
    if method == "max":
        scale = np.max(np.abs(y))
    elif method == "minmax":
        lo, hi = np.min(y), np.max(y)
        span = hi - lo
        if span == 0:
            raise ValueError("cannot minmax-normalize a constant spectrum")
        return s.derive((y - lo) / span, "normalize (minmax)")
    elif method == "area":
        x_asc, y_asc, _ = sort_ascending(s.x, y)
        scale = abs(np.trapezoid(y_asc, x_asc))
    elif method == "vector":
        scale = np.linalg.norm(y)
    elif method == "peak":
        if at is None:
            raise ValueError("method='peak' requires the 'at' x position")
        scale = abs(float(interp_values(np.asarray([at]), s.x, y)[0]))
    else:
        raise ValueError(f"unknown normalize method: {method!r}")

    if scale == 0 or not np.isfinite(scale):
        raise ValueError(f"normalize method {method!r} produced a zero/invalid scale factor")

    return s.derive(y / scale, f"normalize ({method})")
