"""Varian 3100 FT-IR (Excalibur series) driver (real hardware, over USB).

See ``docs/varian-protocol.md`` for the protocol, decoded from USB captures
of Varian Resolutions Pro. The bench firmware is not part of OpenFTIR: the
user supplies the file.
"""

from __future__ import annotations

from .driver import SUPPORTED_RESOLUTIONS, VarianInstrument, scans_to_single_beam
from .protocol import load_firmware
from .transport import UsbTransport, VarianTransport

__all__ = [
    "VarianInstrument",
    "scans_to_single_beam",
    "SUPPORTED_RESOLUTIONS",
    "load_firmware",
    "UsbTransport",
    "VarianTransport",
]
