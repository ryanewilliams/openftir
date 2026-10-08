"""Parser for the Windows USBPcap frame format (Wireshark DLT_USBPCAP, link
type 249), as written by USBPcap's kernel-mode filter driver and captured
raw (no further Wireshark dissection applied) in the pcap/pcapng file.

This is the publicly documented, open-source USBPcap capture format (BSD
licensed, also implemented by Wireshark's own ``packet-usb.c`` dissector) --
a generic Windows USB bus-sniffing format, not anything specific to the
Thermo instrument. Field layout (``USBPCAP_BUFFER_PACKET_HEADER``):

    offset  size  field
    0       2     headerLen   -- length of this header (+ control stage byte)
    2       8     irpId       -- I/O Request Packet id, used to pair submit/
                                 complete records
    10      4     status      -- USB/NT status code (valid on complete)
    14      2     function    -- URB function code
    16      1     info        -- bit0 (0x01) set => PDO-to-FDO direction,
                                 i.e. this record is the *completion*; clear
                                 => FDO-to-PDO, i.e. this is the *submission*
    17      2     bus
    19      2     device
    21      1     endpoint    -- bit7 (0x80) set => IN; low 7 bits = number
    22      1     transfer    -- 0=isochronous 1=interrupt 2=control 3=bulk
    23      4     dataLength

For control transfers, headerLen is 28 (one extra "stage" byte: 0=SETUP,
1=DATA, 2=STATUS) and, on the SETUP stage, the 8-byte USB setup packet is
captured as the first 8 bytes of frame data immediately following the
header.
"""

from __future__ import annotations

import struct

from .model import SetupPacket, TransferType, UsbEvent

_HEADER = struct.Struct("<HQIHBHHBBI")
assert _HEADER.size == 27

_INFO_PDO_TO_FDO = 0x01

_CONTROL_STAGE_SETUP = 0

_SETUP_PACKET = struct.Struct("<BBHHH")
assert _SETUP_PACKET.size == 8


def parse_usbpcap_frame(data: bytes, t: float) -> UsbEvent:
    """Parse one raw USBPcap frame's bytes into a :class:`UsbEvent`."""
    if len(data) < _HEADER.size:
        raise ValueError(f"USBPcap frame too short: {len(data)} bytes")

    (
        header_len,
        irp_id,
        status,
        _function,
        info,
        bus,
        device,
        ep_raw,
        transfer_raw,
        _data_length,
    ) = _HEADER.unpack_from(data, 0)

    if header_len < _HEADER.size or header_len > len(data):
        raise ValueError(f"USBPcap frame has invalid headerLen {header_len}")

    direction_in = bool(ep_raw & 0x80)
    endpoint = ep_raw & 0x7F
    is_submit = not bool(info & _INFO_PDO_TO_FDO)

    payload = data[header_len:]
    setup = None
    if transfer_raw == TransferType.CONTROL and header_len > _HEADER.size:
        stage = data[_HEADER.size]
        if stage == _CONTROL_STAGE_SETUP and len(payload) >= _SETUP_PACKET.size:
            bm_request_type, b_request, w_value, w_index, w_length = _SETUP_PACKET.unpack_from(
                payload, 0
            )
            setup = SetupPacket(bm_request_type, b_request, w_value, w_index, w_length)
            payload = payload[_SETUP_PACKET.size :]

    return UsbEvent(
        t=t,
        bus=bus,
        device=device,
        endpoint=endpoint,
        direction_in=direction_in,
        transfer_type=transfer_raw,
        is_submit=is_submit,
        status=status,
        xfer_id=irp_id,
        data=bytes(payload),
        setup=setup,
        source="usbpcap",
    )
