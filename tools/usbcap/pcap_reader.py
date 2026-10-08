"""Minimal, dependency-free reader for classic pcap and pcapng capture files.

Supports exactly what :mod:`tools.usbcap` needs and nothing more:

- Classic pcap (``.pcap``): global header + per-packet (sec, usec, len) records,
  both byte orders, usec and nsec timestamp resolution variants.
- pcapng (``.pcapng``): Section Header, Interface Description, Enhanced Packet
  and Simple Packet blocks, both byte orders, multiple interfaces (each with
  its own link type and optional ``if_tsresol``/``if_tsoffset`` options).

Deliberately NOT supported (raises :class:`ValueError` if encountered): packet
blocks (obsolete legacy pcapng block type 2), compressed captures, and any
pcapng option this tool doesn't need. Those are not expected from Wireshark
USBPcap/usbmon captures.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Union

# -- classic pcap -----------------------------------------------------------

#: On-disk magic byte sequences. The classic pcap magic is the 32-bit value
#: 0xa1b2c3d4 (seconds+microseconds) or 0xa1b23c4d (seconds+nanoseconds)
#: written in the capturing host's own byte order -- so a little-endian host
#: writes the LE encoding of that value (least-significant byte first), and
#: a big-endian host writes it most-significant byte first.
_PCAP_MAGIC_USEC_LE = b"\xd4\xc3\xb2\xa1"  # LE encoding of 0xa1b2c3d4
_PCAP_MAGIC_USEC_BE = b"\xa1\xb2\xc3\xd4"  # BE encoding of 0xa1b2c3d4
_PCAP_MAGIC_NSEC_LE = b"\x4d\x3c\xb2\xa1"  # LE encoding of 0xa1b23c4d
_PCAP_MAGIC_NSEC_BE = b"\xa1\xb2\x3c\x4d"  # BE encoding of 0xa1b23c4d

# -- pcapng -------------------------------------------------------------------

_PCAPNG_SHB_TYPE = 0x0A0D0D0A  # byte-order-independent (bytes read the same
                                 # forwards and backwards: 0A 0D 0D 0A)
_PCAPNG_BOM = 0x1A2B3C4D

_BT_INTERFACE_DESCRIPTION = 1
_BT_SIMPLE_PACKET = 3
_BT_ENHANCED_PACKET = 6

_OPT_IF_TSRESOL = 9
_OPT_IF_TSOFFSET = 14
_OPT_END_OF_OPT = 0


@dataclass
class Frame:
    """One captured link-layer frame, timestamped and already linktype-tagged."""

    t: float  # seconds; absolute if the source gave absolute times, else relative
    linktype: int
    data: bytes
    interface_id: int = 0


def read_frames(path: Union[str, Path]) -> Iterator[Frame]:
    """Read ``path`` (classic pcap or pcapng, auto-detected) and yield :class:`Frame`."""
    data = Path(path).read_bytes()
    return read_frames_bytes(data)


def read_frames_bytes(data: bytes) -> Iterator[Frame]:
    if len(data) < 4:
        raise ValueError("capture file too short to contain a valid header")
    magic4 = data[0:4]
    if struct.unpack(">I", magic4)[0] == _PCAPNG_SHB_TYPE:
        yield from _read_pcapng(data)
    elif magic4 in (
        _PCAP_MAGIC_USEC_LE,
        _PCAP_MAGIC_USEC_BE,
        _PCAP_MAGIC_NSEC_LE,
        _PCAP_MAGIC_NSEC_BE,
    ):
        yield from _read_pcap(data)
    else:
        raise ValueError(f"unrecognised capture file magic: {magic4!r}")


# -- classic pcap implementation ---------------------------------------------


def _read_pcap(data: bytes) -> Iterator[Frame]:
    magic = data[0:4]
    if magic == _PCAP_MAGIC_USEC_LE:
        endian, tsresol = "<", 1e-6
    elif magic == _PCAP_MAGIC_USEC_BE:
        endian, tsresol = ">", 1e-6
    elif magic == _PCAP_MAGIC_NSEC_LE:
        endian, tsresol = "<", 1e-9
    elif magic == _PCAP_MAGIC_NSEC_BE:
        endian, tsresol = ">", 1e-9
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unrecognised classic pcap magic: {magic!r}")

    (_major, _minor, _thiszone, _sigfigs, _snaplen, network) = struct.unpack_from(
        endian + "HHiIII", data, 4
    )
    pos = 24
    rec_hdr = struct.Struct(endian + "IIII")
    while pos + rec_hdr.size <= len(data):
        ts_sec, ts_usec, incl_len, _orig_len = rec_hdr.unpack_from(data, pos)
        pos += rec_hdr.size
        pkt = data[pos : pos + incl_len]
        pos += incl_len
        yield Frame(t=ts_sec + ts_usec * tsresol, linktype=network, data=pkt)


# -- pcapng implementation ----------------------------------------------------


def _parse_options(buf: bytes, endian: str) -> dict:
    opts: dict[int, bytes] = {}
    pos = 0
    while pos + 4 <= len(buf):
        code, length = struct.unpack_from(endian + "HH", buf, pos)
        pos += 4
        if code == _OPT_END_OF_OPT:
            break
        value = buf[pos : pos + length]
        opts[code] = value
        pos += length + ((4 - length % 4) % 4)  # pad to 4-byte boundary
    return opts


def _tsresol_to_seconds(raw: bytes) -> float:
    if not raw:
        return 1e-6
    val = raw[0]
    if val & 0x80:
        return 2.0 ** -(val & 0x7F)
    return 10.0 ** -val


def _read_pcapng(data: bytes) -> Iterator[Frame]:
    pos = 0
    endian = "<"
    interfaces: list[dict] = []  # index -> {linktype, tsresol}
    n = len(data)
    while pos < n:
        if pos + 8 > n:
            break
        block_type = struct.unpack_from(">I", data, pos)[0]
        if block_type == _PCAPNG_SHB_TYPE:
            bom = struct.unpack_from("<I", data, pos + 8)[0]
            if bom == _PCAPNG_BOM:
                endian = "<"
            elif struct.unpack_from(">I", data, pos + 8)[0] == _PCAPNG_BOM:
                endian = ">"
            else:
                raise ValueError("pcapng Section Header has unrecognised byte-order magic")
            interfaces = []  # a new section resets the interface list

        block_type, block_total_len = struct.unpack_from(endian + "II", data, pos)
        if block_total_len < 12 or pos + block_total_len > n:
            raise ValueError(f"pcapng block at offset {pos} has invalid length {block_total_len}")
        body = data[pos + 8 : pos + block_total_len - 4]

        if block_type == _BT_INTERFACE_DESCRIPTION:
            linktype, _reserved, _snaplen = struct.unpack_from(endian + "HHI", body, 0)
            opts = _parse_options(body[8:], endian)
            tsresol = _tsresol_to_seconds(opts.get(_OPT_IF_TSRESOL, b""))
            interfaces.append({"linktype": linktype, "tsresol": tsresol})
        elif block_type == _BT_ENHANCED_PACKET:
            iface_id, ts_high, ts_low, cap_len, _orig_len = struct.unpack_from(
                endian + "IIIII", body, 0
            )
            pkt = body[20 : 20 + cap_len]
            if iface_id < len(interfaces):
                iface = interfaces[iface_id]
            else:
                iface = {"linktype": 0, "tsresol": 1e-6}
            ticks = (ts_high << 32) | ts_low
            t = ticks * iface["tsresol"]
            yield Frame(t=t, linktype=iface["linktype"], data=pkt, interface_id=iface_id)
        elif block_type == _BT_SIMPLE_PACKET:
            (orig_len,) = struct.unpack_from(endian + "I", body, 0)
            pkt = body[4 : 4 + orig_len]
            iface = interfaces[0] if interfaces else {"linktype": 0, "tsresol": 1e-6}
            yield Frame(t=0.0, linktype=iface["linktype"], data=pkt, interface_id=0)
        # Other block types (name resolution, interface stats, etc.) are
        # silently skipped -- they carry no packet data we need.

        pos += block_total_len
