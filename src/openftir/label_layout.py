"""Peak-label placement shared by the on-screen plot and the PDF report.

Each label starts just below its marker (above it for absorbance peaks). If
that spot would cover a spectrum trace, another label, a marker or the plot
edge, it moves to the nearest clear spot -- further down, or sideways and
down -- and the caller draws a thin leader line back to the peak.

Everything here is in pixels with y increasing downwards, so it works the
same for pyqtgraph and matplotlib once each maps its data to pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

#: gap between a marker and the label in its default spot, pixels
GAP = 3.0
#: labels keep this far apart from each other, pixels
_LABEL_PAD = 2.0
#: clearance kept between a label and a trace, pixels
_CURVE_PAD = 1.0
#: furthest a label may drop below its default spot, in label heights
_MAX_DROP_HEIGHTS = 8
#: sideways shifts tried, in label widths (each also tried with every drop)
_X_SHIFTS = (0.0, 0.6, 1.1, 1.7)
#: a sideways pixel costs this many vertical pixels when ranking spots
_X_COST = 1.6


@dataclass(frozen=True)
class LabelRequest:
    """One label to place: its marker position and text size, in pixels."""

    x: float
    y: float
    width: float
    height: float
    #: True for %T/%R dips (label under the marker), False for absorbance peaks
    below: bool = True
    #: manual picks are always placed, even when no spot is clear
    must_show: bool = False


@dataclass(frozen=True)
class LabelPlacement:
    left: float
    top: float
    width: float
    height: float
    #: True when the label sits away from its marker and needs a leader line
    leader: bool

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def anchor_point(self, below: bool) -> tuple[float, float]:
        """Where a leader line meets the label: the middle of the edge
        facing the marker."""
        return (self.center_x, self.top if below else self.bottom)


class _Curve:
    """A trace as line segments, sorted by x for fast rectangle queries."""

    def __init__(self, xs: np.ndarray, ys: np.ndarray) -> None:
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        keep = np.isfinite(xs) & np.isfinite(ys)
        xs, ys = xs[keep], ys[keep]
        order = np.argsort(xs, kind="stable")
        xs, ys = xs[order], ys[order]
        self.x0, self.x1 = xs[:-1], xs[1:]
        self.y0, self.y1 = ys[:-1], ys[1:]

    def hits(self, left: float, top: float, right: float, bottom: float) -> bool:
        if not len(self.x0):
            return False
        lo = int(np.searchsorted(self.x1, left, side="left"))
        hi = int(np.searchsorted(self.x0, right, side="right"))
        if hi <= lo:
            return False
        x0, x1 = self.x0[lo:hi], self.x1[lo:hi]
        y0, y1 = self.y0[lo:hi], self.y1[lo:hi]
        # Clip each segment to [left, right] and take its y span there.
        dx = x1 - x0
        with np.errstate(invalid="ignore", divide="ignore"):
            slope = np.where(dx > 0, (y1 - y0) / dx, 0.0)
        ya = y0 + slope * (np.clip(left, x0, x1) - x0)
        yb = y0 + slope * (np.clip(right, x0, x1) - x0)
        seg_top = np.minimum(ya, yb)
        seg_bottom = np.maximum(ya, yb)
        return bool(np.any((seg_bottom >= top) & (seg_top <= bottom)))


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float], pad: float) -> bool:
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def _segment_hits_rect(p: tuple[float, float], q: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
    """Liang-Barsky: does the segment p-q pass through ``rect``?"""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for edge_p, edge_q in ((-dx, x0 - rect[0]), (dx, rect[2] - x0), (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if edge_p == 0:
            if edge_q < 0:
                return False
            continue
        t = edge_q / edge_p
        if edge_p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _needs_leader(req: LabelRequest, left: float, top: float) -> bool:
    right, bottom = left + req.width, top + req.height
    dx = max(left - req.x, 0.0, req.x - right)
    dy = max(top - req.y, 0.0, req.y - bottom)
    return math.hypot(dx, dy) > GAP + 4.0


def place_labels(
    requests: Sequence[LabelRequest],
    curves: Sequence[tuple[np.ndarray, np.ndarray]],
    bounds: tuple[float, float, float, float],
    marker_radius: float = 3.0,
) -> list[LabelPlacement | None]:
    """Place each label in turn (earlier requests win the better spots).

    ``bounds`` is the plot area as (left, top, right, bottom). Returns one
    placement per request, or None for a label that could not be placed
    without covering another label (never for ``must_show`` requests).
    """
    b_left, b_top, b_right, b_bottom = bounds
    traces = [_Curve(x, y) for x, y in curves]
    markers = [
        (r.x - marker_radius, r.y - marker_radius, r.x + marker_radius, r.y + marker_radius) for r in requests
    ]
    placed: list[tuple[float, float, float, float]] = []
    leaders: list[tuple[tuple[float, float], tuple[float, float]]] = []
    results: list[LabelPlacement | None] = []

    for req in requests:
        w, h = req.width, req.height
        default_top = req.y + GAP if req.below else req.y - GAP - h
        step = max(2.0, h / 2)
        direction = 1.0 if req.below else -1.0
        candidates = []
        for k in range(int(_MAX_DROP_HEIGHTS * h / step) + 1):
            dy = k * step
            for shift in _X_SHIFTS:
                for sign in ((1.0,) if shift == 0 else (1.0, -1.0)):
                    dx = sign * shift * w
                    candidates.append((dy + _X_COST * abs(dx), req.x + dx - w / 2, default_top + direction * dy))
        # Last resort for a band at the very edge of the plot: beside the
        # marker, moved back towards it.
        for k in range(1, int(2 * h / step) + 2):
            dy = k * step
            for shift in _X_SHIFTS[1:]:
                for sign in (1.0, -1.0):
                    dx = sign * shift * w
                    cost = 1000.0 + dy + _X_COST * abs(dx)
                    candidates.append((cost, req.x + dx - w / 2, default_top - direction * dy))
        candidates.sort(key=lambda c: c[0])

        choice = None
        fallback = None
        for _cost, left, top in candidates:
            rect = (left, top, left + w, top + h)
            if rect[0] < b_left or rect[2] > b_right or rect[1] < b_top or rect[3] > b_bottom:
                continue
            if any(_overlaps(rect, other, _LABEL_PAD) for other in placed):
                continue
            if any(_overlaps(rect, m, 0.0) for m in markers):
                continue
            if any(_segment_hits_rect(p, q, rect) for p, q in leaders):
                continue
            if _needs_leader(req, left, top):
                end = (left + w / 2, top if req.below else top + h)
                if any(_segment_hits_rect((req.x, req.y), end, other) for other in placed):
                    continue
            padded = (rect[0] - _CURVE_PAD, rect[1] - _CURVE_PAD, rect[2] + _CURVE_PAD, rect[3] + _CURVE_PAD)
            if any(t.hits(*padded) for t in traces):
                if fallback is None:
                    fallback = (left, top)
                continue
            choice = (left, top)
            break

        if choice is None:
            # Nothing completely clear: better a label touching the trace
            # than a listed peak with no label at all.
            choice = fallback
        if choice is None and req.must_show:
            left = min(max(req.x - w / 2, b_left), b_right - w)
            top = min(max(default_top, b_top), b_bottom - h)
            choice = (left, top)
        if choice is None:
            results.append(None)
            continue
        left, top = choice
        placed.append((left, top, left + w, top + h))
        placement = LabelPlacement(left, top, w, h, leader=_needs_leader(req, left, top))
        if placement.leader:
            leaders.append(((req.x, req.y), placement.anchor_point(req.below)))
        results.append(placement)
    return results
