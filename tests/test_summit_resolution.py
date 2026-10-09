"""Tests for ``openftir.instruments.summit.resolution``."""

from __future__ import annotations

import logging

from openftir.instruments.summit.resolution import (
    SUPPORTED_RESOLUTIONS,
    UNVERIFIED_RESOLUTIONS,
    scan_geometry,
)

_LASER_FREQ = 11685.125977  # the user's Summit X, docs/summit-protocol.md


def test_exact_table_4cm():
    assert scan_geometry(4.0, _LASER_FREQ) == (6154, 12426)


def test_exact_table_8cm():
    assert scan_geometry(8.0, _LASER_FREQ) == (3124, 6367)


def test_sdp_is_2_peakpos_plus_118_for_the_4cm_entry():
    # Holds exactly for the 4 cm-1 capture. The 8 cm-1 capture's exact sdp
    # (6367) is one more than the formula gives (6366) -- the formula is
    # docs/summit-protocol.md's *approximate* pattern, not an identity the
    # hard-coded exact table is required to satisfy.
    peak_pos, sdp = scan_geometry(4.0, _LASER_FREQ)
    assert sdp == 2 * peak_pos + 118


def test_four_is_first_and_supported_resolutions_match_spec():
    assert SUPPORTED_RESOLUTIONS[0] == 4.0
    assert set(SUPPORTED_RESOLUTIONS) == {4.0, 8.0, 16.0, 2.0}


def test_unverified_resolutions_excludes_4_and_8():
    assert 4.0 not in UNVERIFIED_RESOLUTIONS
    assert 8.0 not in UNVERIFIED_RESOLUTIONS
    assert set(UNVERIFIED_RESOLUTIONS) == {16.0, 2.0}


def test_extrapolation_formula_for_unverified_resolution():
    peak_pos, sdp = scan_geometry(16.0, _LASER_FREQ)
    expected_peak = round(1.0372 * (2 * _LASER_FREQ / 16.0) + 94.0)
    assert peak_pos == expected_peak
    assert sdp == 2 * expected_peak + 118


def test_extrapolation_warns(caplog):
    with caplog.at_level(logging.WARNING):
        scan_geometry(16.0, _LASER_FREQ)
    assert any("unverified" in rec.message for rec in caplog.records)


def test_exact_table_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        scan_geometry(4.0, _LASER_FREQ)
    assert not any("unverified" in rec.message for rec in caplog.records)


def test_sdp_clamped_to_sdp_range():
    # Force a huge sdp via a tiny resolution, then clamp it down.
    peak_pos, sdp = scan_geometry(0.5, _LASER_FREQ, sdp_range=(256, 10000))
    assert sdp == 10000
    # peakPos itself is never clamped (docs/summit-protocol.md notes Paradigm
    # itself sends a peakPos beyond the device's own peakPosCfg max).
    assert peak_pos == round(1.0372 * (2 * _LASER_FREQ / 0.5) + 94.0)


def test_sdp_clamped_to_minimum():
    peak_pos, sdp = scan_geometry(100.0, _LASER_FREQ, sdp_range=(5000, 68000))
    assert sdp == 5000
