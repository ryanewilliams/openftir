"""Resolution -> scan-length mapping (``docs/summit-protocol.md``, "Resolution
-> scan length").

Only 4 and 8 cm-1 have been seen in a real capture; the exact ``peakPos``
(centre-burst index) / ``sdp`` (total samples per scan) pairs for those are
hard-coded from the two captures analysed. Every other resolution falls back
to the doc's two-point extrapolation, which is explicitly marked unverified
there -- callers should surface that to the user (see
:data:`UNVERIFIED_RESOLUTIONS` and the driver's ``InstrumentInfo.extra``).
"""

from __future__ import annotations

import logging

__all__ = [
    "SUPPORTED_RESOLUTIONS",
    "VERIFIED_RESOLUTIONS",
    "UNVERIFIED_RESOLUTIONS",
    "scan_geometry",
]

log = logging.getLogger(__name__)

#: (peakPos, sdp) seen directly in captures 03/04/06.
_EXACT_TABLE: dict[float, tuple[int, int]] = {
    4.0: (6154, 12426),
    8.0: (3124, 6367),
}

#: Resolutions offered in ``InstrumentInfo.resolutions``, 4 cm-1 (the
#: default/most-used, and one of the two confirmed values) listed first.
SUPPORTED_RESOLUTIONS: tuple[float, ...] = (4.0, 8.0, 16.0, 2.0)

#: Resolutions whose peakPos/sdp were actually observed in a capture.
VERIFIED_RESOLUTIONS: frozenset[float] = frozenset(_EXACT_TABLE)

UNVERIFIED_RESOLUTIONS: tuple[float, ...] = tuple(
    r for r in SUPPORTED_RESOLUTIONS if r not in VERIFIED_RESOLUTIONS
)


def scan_geometry(
    resolution: float,
    laser_freq: float,
    sdp_range: tuple[float, float] | None = None,
) -> tuple[int, int]:
    """Return ``(peakPos, sdp)`` for ``resolution`` (cm-1).

    4 and 8 cm-1 use the exact values seen in captures. Any other resolution
    uses the doc's unverified extrapolation ``peakPos = round(1.0372 *
    (2*laser_freq/resolution) + 94)``, ``sdp = 2*peakPos + 118``, and logs a
    warning. ``sdp`` is clamped to ``sdp_range`` (the instrument's own
    ``sdpCfg`` min/max, fetched at connect) if given -- ``peakPos`` is
    deliberately never clamped: the doc notes Paradigm itself sends a
    peakPos (6154) that exceeds the device's own ``peakPosCfg`` max.
    """
    if resolution in _EXACT_TABLE:
        return _EXACT_TABLE[resolution]

    log.warning(
        "Summit: no captured peakPos/sdp for resolution %.3g cm-1; using the "
        "unverified extrapolation from docs/summit-protocol.md",
        resolution,
    )
    peak_pos = round(1.0372 * (2.0 * laser_freq / resolution) + 94.0)
    sdp = 2 * peak_pos + 118
    if sdp_range is not None:
        lo, hi = sdp_range
        sdp = max(int(round(lo)), min(int(round(hi)), sdp))
    return int(peak_pos), int(sdp)
