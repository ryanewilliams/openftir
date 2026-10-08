"""Parser for the Linux usbmon binary frame format (Wireshark
DLT_USB_LINUX / DLT_USB_LINUX_MMAPPED, link types 189 and 220).

This is the public kernel ``struct usbmon_packet`` layout documented in
``Documentation/usb/usbmon.rst``. Two on-the-wire sizes exist:

- 48 bytes (DLT_USB_LINUX, the older/simpler capture format): everything up
  to and including the ``setup``/iso union, no isochronous descriptor count
  or interval/start_frame/xfer_flags fields.
- 64 bytes (DLT_USB_LINUX_MMAPPED): the full struct, with interval,
  start_frame, xfer_flags and ndesc appended.

Field layout (64-byte form; the 48-byte form is a truncation at offset 48):

    offset  size  field
    0       8     id          -- URB id, used to pair submit/complete records
    8       1     type        -- 'S' submit, 'C' complete, 'E' error
    9       1     xfer_type   -- 0=isochronous 1=interrupt 2=control 3=bulk
    10      1     epnum       -- bit7 (0x80) set => IN; low 7 bits = number
    11      1     devnum
    12      2     busnum
    14      1     flag_setup  -- 0 => the 8-byte setup field below is valid
    15      1     flag_data   -- 0 => data was captured (len_cap bytes follow)
    16      8     ts_sec
    24      4     ts_usec
    28      4     status
    32      4     length      -- requested/actual URB length
    36      4     len_cap     -- bytes of data actually captured, follow the header
    40      8     setup       -- raw 8-byte setup packet (control only) / iso info
    48      4     interval    (64-byte form only)
    52      4     start_frame (64-byte form only)
    56      4     xfer_flags  (64-byte form only)
    60      4     ndesc       (64-byte form only)
"""

from __future__ import annotations

import struct

from .model import DLT_USB_LINUX_MMAPPED, SetupPacket, UsbEvent

_HEADER_64 = struct.Struct("<QBBBBHbbqiiII8siiII")
assert _HEADER_64.size == 64

_HEADER_48 = struct.Struct("<QBBBBHbbqiiII8s")
assert _HEADER_48.size == 48

_SETUP_PACKET = struct.Struct("<BBHHH")
assert _SETUP_PACKET.size == 8

_TYPE_SUBMIT = ord("S")


def parse_usbmon_frame(data: bytes, t: float, linktype: int = DLT_USB_LINUX_MMAPPED) -> UsbEvent:
    """Parse one raw usbmon frame's bytes into a :class:`UsbEvent`.

    Which header size (48 or 64 bytes) applies is determined by the pcap
    link type (DLT_USB_LINUX vs DLT_USB_LINUX_MMAPPED), NOT by guessing from
    the frame's total length -- a 48-byte-header frame can easily carry a
    payload that makes its total length exceed 64 bytes, so length alone is
    not a reliable discriminator.
    """
    header = _HEADER_64 if linktype == DLT_USB_LINUX_MMAPPED else _HEADER_48
    if len(data) < header.size:
        raise ValueError(f"usbmon frame too short: {len(data)} bytes (need >= {header.size})")

    (
        urb_id,
        pkt_type,
        xfer_type,
        epnum,
        devnum,
        busnum,
        flag_setup,
        flag_data,
        _ts_sec,
        _ts_usec,
        status,
        _length,
        len_cap,
        setup_bytes,
        *_rest,
    ) = header.unpack_from(data, 0)

    direction_in = bool(epnum & 0x80)
    endpoint = epnum & 0x7F
    is_submit = pkt_type == _TYPE_SUBMIT

    payload_offset = header.size
    payload = data[payload_offset : payload_offset + len_cap] if flag_data == 0 else b""

    setup = None
    if flag_setup == 0 and len(setup_bytes) == _SETUP_PACKET.size:
        bm_request_type, b_request, w_value, w_index, w_length = _SETUP_PACKET.unpack(
            setup_bytes
        )
        setup = SetupPacket(bm_request_type, b_request, w_value, w_index, w_length)

    return UsbEvent(
        t=t,
        bus=busnum,
        device=devnum,
        endpoint=endpoint,
        direction_in=direction_in,
        transfer_type=xfer_type,
        is_submit=is_submit,
        status=status,
        xfer_id=urb_id,
        data=bytes(payload),
        setup=setup,
        source="usbmon",
    )
