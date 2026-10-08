"""Tests for the Linux usbmon frame parser (DLT_USB_LINUX / DLT_USB_LINUX_MMAPPED),
using a synthetic pcapng capture (no real hardware trace exists yet).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.usbcap.analysis import load_capture, summarize
from tools.usbcap.model import DLT_USB_LINUX, DLT_USB_LINUX_MMAPPED, TransferType
from tools.usbcap.synth import build_pcapng

THERMO_VID = 0x0EBB
THERMO_PID = 0x000C

_HDR_64 = struct.Struct("<QBBBBHbbqiiII8siiII")
_HDR_48 = struct.Struct("<QBBBBHbbqiiII8s")


def usbmon_frame(
    urb_id: int,
    is_submit: bool,
    xfer_type: int,
    epnum: int,
    direction_in: bool,
    devnum: int,
    busnum: int,
    status: int = 0,
    data: bytes = b"",
    setup: bytes | None = None,
    use_64: bool = True,
) -> bytes:
    pkt_type = ord("S") if is_submit else ord("C")
    ep_byte = (0x80 if direction_in else 0x00) | (epnum & 0x7F)
    flag_setup = 0 if setup is not None else ord("-")
    flag_data = 0 if data else ord("<")
    setup_bytes = setup if setup is not None else b"\x00" * 8
    length = len(data)
    len_cap = len(data)

    common = (
        urb_id,
        pkt_type,
        xfer_type,
        ep_byte,
        devnum,
        busnum,
        flag_setup,
        flag_data,
        0,  # ts_sec (we rely on the pcapng frame timestamp instead)
        0,  # ts_usec
        status,
        length,
        len_cap,
        setup_bytes,
    )
    if use_64:
        header = _HDR_64.pack(*common, 0, 0, 0, 0)
    else:
        header = _HDR_48.pack(*common)
    return header + data


def device_descriptor_bytes(vid: int, pid: int) -> bytes:
    return struct.pack(
        "<BBHBBBBHHHBBBB", 18, 1, 0x0200, 0, 0, 0, 64, vid, pid, 0x0100, 0, 0, 0, 1
    )


@pytest.mark.parametrize("use_64", [True, False])
def test_usbmon_enumeration_and_bulk(tmp_path, use_64):
    setup = struct.pack("<BBHHH", 0x80, 0x06, 0x0100, 0, 18)
    frames = [
        (usbmon_frame(1, True, 2, 0, True, 5, 1, setup=setup, use_64=use_64), 0.0),
        (
            usbmon_frame(
                1, False, 2, 0, True, 5, 1, setup=setup, data=device_descriptor_bytes(THERMO_VID, THERMO_PID), use_64=use_64
            ),
            0.001,
        ),
        (usbmon_frame(2, True, 3, 1, False, 5, 1, data=b"\x10\x01\x00\x00\x01\x00\x00\x00", use_64=use_64), 0.01),
        (usbmon_frame(2, False, 3, 1, False, 5, 1, use_64=use_64), 0.0105),
        (usbmon_frame(3, True, 3, 2, True, 5, 1, use_64=use_64), 0.02),
        (usbmon_frame(3, False, 3, 2, True, 5, 1, data=b"\x01\x02\x03\x04" * 8, use_64=use_64), 0.0205),
    ]

    linktype = DLT_USB_LINUX_MMAPPED if use_64 else DLT_USB_LINUX
    pcapng = build_pcapng(frames, linktype=linktype)
    path = tmp_path / "cap.pcapng"
    path.write_bytes(pcapng)

    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    assert (1, 5) in cap.all_devices
    dd = cap.all_devices[(1, 5)].device_descriptor
    assert dd is not None and (dd.vendor_id, dd.product_id) == (THERMO_VID, THERMO_PID)

    bulk_out = next(tr for tr in cap.transfers if tr.transfer_type == TransferType.BULK and not tr.direction_in)
    assert bulk_out.data == b"\x10\x01\x00\x00\x01\x00\x00\x00"

    bulk_in = next(tr for tr in cap.transfers if tr.transfer_type == TransferType.BULK and tr.direction_in)
    assert bulk_in.data == b"\x01\x02\x03\x04" * 8
    assert bulk_in.endpoint == 2

    text = summarize(cap)
    assert "0ebb:000c" in text
