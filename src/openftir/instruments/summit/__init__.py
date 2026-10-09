"""Thermo Nicolet Summit X driver (real hardware, over USB).

See ``docs/summit-protocol.md`` for the reverse-engineered wire protocol
this package implements, and ``docs/adding-a-driver.md`` for how it fits
the rest of OpenFTIR.
"""

from __future__ import annotations

from .driver import SummitInstrument, coadd_interferogram, coadd_to_single_beam
from .resolution import SUPPORTED_RESOLUTIONS, UNVERIFIED_RESOLUTIONS, scan_geometry
from .transport import SummitTransport, UsbTransport

__all__ = [
    "SummitInstrument",
    "coadd_interferogram",
    "coadd_to_single_beam",
    "SUPPORTED_RESOLUTIONS",
    "UNVERIFIED_RESOLUTIONS",
    "scan_geometry",
    "SummitTransport",
    "UsbTransport",
]
