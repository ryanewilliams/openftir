"""Synthetic pcapng capture writer -- TEST SUPPORT ONLY.

Builds minimal-but-valid pcapng files containing synthetic USBPcap (or
usbmon) frames, so :mod:`tests.test_usbcap` can exercise the reader/parser/
analysis code without needing a real capture. Not used by the CLI or by any
real analysis path.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from .model import DLT_USBPCAP

_SHB_TYPE = 0x0A0D0D0A
_IDB_TYPE = 1
_EPB_TYPE = 6
_BOM = 0x1A2B3C4D


def _pad4(b: bytes) -> bytes:
    pad = (-len(b)) % 4
    return b + b"\x00" * pad


def build_shb(endian: str = "<") -> bytes:
    body = struct.pack(endian + "IHHq", _BOM, 1, 0, -1)
    total_len = 12 + len(body)
    return (
        struct.pack(endian + "II", _SHB_TYPE, total_len)
        + body
        + struct.pack(endian + "I", total_len)
    )


def build_idb(linktype: int, endian: str = "<", tsresol_pow10: int = 6) -> bytes:
    opt_value = struct.pack("B", tsresol_pow10)
    opt = struct.pack(endian + "HH", 9, 1) + _pad4(opt_value)
    end_opt = struct.pack(endian + "HH", 0, 0)
    body = struct.pack(endian + "HHI", linktype, 0, 65535) + opt + end_opt
    total_len = 12 + len(body)
    return (
        struct.pack(endian + "II", _IDB_TYPE, total_len)
        + body
        + struct.pack(endian + "I", total_len)
    )


def build_epb(data: bytes, t_seconds: float, endian: str = "<", interface_id: int = 0, tsresol: float = 1e-6) -> bytes:
    ticks = int(round(t_seconds / tsresol))
    ts_high = (ticks >> 32) & 0xFFFFFFFF
    ts_low = ticks & 0xFFFFFFFF
    cap_len = len(data)
    padded = _pad4(data)
    body = struct.pack(endian + "IIIII", interface_id, ts_high, ts_low, cap_len, cap_len) + padded
    opts_end = struct.pack(endian + "HH", 0, 0)
    body += opts_end
    total_len = 12 + len(body)
    return (
        struct.pack(endian + "II", _EPB_TYPE, total_len)
        + body
        + struct.pack(endian + "I", total_len)
    )


def build_pcapng(frames: list[tuple[bytes, float]], linktype: int = DLT_USBPCAP, endian: str = "<") -> bytes:
    """``frames``: list of (raw_link_layer_bytes, timestamp_seconds)."""
    out = bytearray()
    out += build_shb(endian=endian)
    out += build_idb(linktype, endian=endian)
    for data, t in frames:
        out += build_epb(data, t, endian=endian)
    return bytes(out)


# -- USBPcap frame builders ---------------------------------------------------

_USBPCAP_HEADER_LEN = 27


def usbpcap_frame(
    irp_id: int,
    bus: int,
    device: int,
    endpoint: int,
    direction_in: bool,
    transfer_type: int,
    is_submit: bool,
    status: int = 0,
    data: bytes = b"",
    setup: Optional[bytes] = None,
) -> bytes:
    """Build one raw USBPcap frame (header + optional control stage byte +
    setup packet/data)."""
    info = 0 if is_submit else 0x01  # bit0: PDO->FDO => this is the completion
    ep_byte = (0x80 if direction_in else 0x00) | (endpoint & 0x7F)
    function = 0

    if transfer_type == 2 and setup is not None:  # CONTROL + SETUP stage
        header_len = _USBPCAP_HEADER_LEN + 1
        header = struct.pack(
            "<HQIHBHHBBI",
            header_len,
            irp_id,
            status,
            function,
            info,
            bus,
            device,
            ep_byte,
            transfer_type,
            len(setup) + len(data),
        )
        stage_byte = struct.pack("<B", 0)  # SETUP stage
        return header + stage_byte + setup + data

    header_len = _USBPCAP_HEADER_LEN
    header = struct.pack(
        "<HQIHBHHBBI",
        header_len,
        irp_id,
        status,
        function,
        info,
        bus,
        device,
        ep_byte,
        transfer_type,
        len(data),
    )
    return header + data


def usbpcap_setup_packet(bm_request_type: int, b_request: int, w_value: int, w_index: int, w_length: int) -> bytes:
    return struct.pack("<BBHHH", bm_request_type, b_request, w_value, w_index, w_length)


@dataclass
class ControlTransferFrames:
    """Convenience: the (submit, complete) frame pair for a simple IN control
    transfer (SETUP stage carries the request; the DATA/STATUS stages are
    folded into the same submit/complete pair for simplicity, with the
    response payload attached to the completion record)."""

    irp_id: int
    bus: int
    device: int
    setup: bytes
    response_data: bytes = b""

    def frames(self) -> list[bytes]:
        submit = usbpcap_frame(
            self.irp_id, self.bus, self.device, endpoint=0, direction_in=True,
            transfer_type=2, is_submit=True, setup=self.setup, data=b"",
        )
        complete = usbpcap_frame(
            self.irp_id, self.bus, self.device, endpoint=0, direction_in=True,
            transfer_type=2, is_submit=False, setup=self.setup, data=self.response_data,
        )
        return [submit, complete]
