import numpy as np

from openftir.label_layout import LabelRequest, _Curve, place_labels

BOUNDS = (0.0, 0.0, 800.0, 400.0)


def _rect(p):
    return (p.left, p.top, p.right, p.bottom)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _v_dip(center, depth_px, half_width, baseline=50.0):
    """A V-shaped %T dip in pixel space (y grows downwards)."""
    xs = np.linspace(0, 800, 1601)
    ys = baseline + np.clip(depth_px * (1 - np.abs(xs - center) / half_width), 0, None)
    return xs, ys


def test_label_goes_straight_under_an_isolated_dip():
    xs, ys = _v_dip(400, 200, 20)
    [p] = place_labels([LabelRequest(400, 250, 30, 12)], [(xs, ys)], BOUNDS)
    assert abs(p.center_x - 400) < 1e-6
    assert p.top > 250
    assert not p.leader


def test_label_moves_off_a_trace_running_underneath():
    # A shallow dip at x=400 sitting beside a much deeper one at x=410: the
    # deep band's walls run straight through the shallow dip's default spot.
    xs = np.linspace(0, 800, 1601)
    ys = 50 + np.clip(60 * (1 - np.abs(xs - 400) / 12), 0, None)
    deep = 50 + np.clip(300 * (1 - np.abs(xs - 412) / 10), 0, None)
    ys = np.maximum(ys, deep)
    req = LabelRequest(400, float(ys[800]), 30, 12)
    [p] = place_labels([req], [(xs, ys)], BOUNDS)
    curve = _Curve(xs, ys)
    assert not curve.hits(*_rect(p))


def test_close_peaks_all_get_labels_without_overlapping():
    xs = np.linspace(0, 800, 1601)
    ys = np.full_like(xs, 50.0)
    reqs = [LabelRequest(x, 200, 30, 12) for x in (300, 310, 320, 330)]
    placements = place_labels(reqs, [(xs, ys)], BOUNDS)
    assert all(p is not None for p in placements)
    rects = [_rect(p) for p in placements]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not _overlap(a, b)


def test_labels_stay_inside_the_plot():
    xs = np.linspace(0, 800, 1601)
    ys = np.full_like(xs, 50.0)
    reqs = [LabelRequest(5, 390, 30, 12), LabelRequest(795, 200, 30, 12)]
    for p in place_labels(reqs, [(xs, ys)], BOUNDS):
        assert p.left >= 0 and p.right <= 800 and p.top >= 0 and p.bottom <= 400


def test_manual_label_is_always_placed_even_with_no_room():
    reqs = [LabelRequest(400, 200, 760, 380), LabelRequest(400, 200, 30, 12, must_show=True)]
    placements = place_labels(reqs, [], BOUNDS)
    assert placements[1] is not None


def test_absorbance_labels_go_above_the_peak():
    [p] = place_labels([LabelRequest(400, 200, 30, 12, below=False)], [], BOUNDS)
    assert p.bottom < 200
