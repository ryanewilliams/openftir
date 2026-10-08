"""Smoothing and numerical differentiation."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

from openftir.spectrum import Spectrum

__all__ = ["savgol", "derivative", "moving_average"]


def _odd_window(window: int, n: int) -> int:
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = min(window, n if n % 2 == 1 else n - 1)
    return max(window, 3)


def savgol(s: Spectrum, window: int = 11, order: int = 2) -> Spectrum:
    """Savitzky-Golay smoothing.

    Operates on ``s.y`` in its existing order, so ascending or descending x
    axes both work (the filter only assumes uniform sample spacing, which
    is independent of direction).
    """
    w = _odd_window(window, len(s))
    polyorder = min(order, w - 1)
    y = savgol_filter(s.y, w, polyorder)
    return s.derive(y, f"Savitzky-Golay smooth (window={w}, order={polyorder})")


def derivative(s: Spectrum, order: int = 1, window: int = 11, polyorder: int = 2) -> Spectrum:
    """``order``-th derivative via a Savitzky-Golay filter.

    Correctly accounts for descending x axes: the raw array derivative is
    computed with a positive sample spacing and then sign-flipped for
    odd-order derivatives when x is actually decreasing (d/dx reverses sign
    under axis reversal for odd orders, not for even ones).
    """
    n = len(s)
    w = _odd_window(window, n)
    po = min(polyorder, w - 1)
    po = max(po, order)

    spacing = float(np.median(np.diff(s.x))) if n > 1 else 1.0
    dx = abs(spacing) if spacing != 0 else 1.0

    y = savgol_filter(s.y, w, po, deriv=order, delta=dx)
    if spacing < 0 and order % 2 == 1:
        y = -y
    return s.derive(y, f"{order}-order derivative (Savitzky-Golay, window={w})")


def moving_average(s: Spectrum, window: int = 5) -> Spectrum:
    """Simple moving-average (boxcar) smoothing with edge-nearest padding."""
    w = max(int(window), 1)
    y = uniform_filter1d(s.y, size=w, mode="nearest")
    return s.derive(y, f"moving average (window={w})")
