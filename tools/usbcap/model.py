"""Protocol-agnostic USB event/transfer model shared by the USBPcap and usbmon
frame parsers, plus submit/complete pairing.

A single USB *transfer* (one IRP on Windows, one URB on Linux) is usually
observed as two capture records: a "submit" (request going down to the host
controller) and a "complete" (result coming back up), carrying the same
opaque 64-bit id (``irpId`` for USBPcap, URB ``id`` for usbmon). We parse both
kinds into a :class:`UsbEvent` each, then :func:`pair_events` merges same-id
events into one :class:`Transfer` with a single payload.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from .pcap_reader import Frame

#: Wireshark link-layer type (DLT) numbers this tool understands.
DLT_USBPCAP = 249
DLT_USB_LINUX = 189
DLT_USB_LINUX_MMAPPED = 220


class TransferType(IntEnum):
    """USB transfer type, using the USBPcap/usbmon *capture* numbering.

    NB: this is NOT the same numbering as the ``bmAttributes`` transfer-type
    bits in a USB endpoint descriptor (there: 0=control, 1=isochronous,
    2=bulk, 3=interrupt). Don't mix the two up -- :mod:`tools.usbcap.descriptors`
    keeps the descriptor-side bits separate for exactly this reason.
    """

    ISOCHRONOUS = 0
    INTERRUPT = 1
    CONTROL = 2
    BULK = 3

    @classmethod
    def label(cls, value: int) -> str:
        try:
            return cls(value).name.lower()
        except ValueError:
            return f"unknown({value})"


@dataclass(frozen=True)
class SetupPacket:
    """The 8-byte USB control transfer setup packet."""

    bm_request_type: int
    b_request: int
    w_value: int
    w_index: int
    w_length: int

    @property
    def direction_in(self) -> bool:
        return bool(self.bm_request_type & 0x80)

    @property
    def descriptor_type(self) -> int:
        """High byte of wValue, meaningful only for GET_DESCRIPTOR requests."""
        return (self.w_value >> 8) & 0xFF

    @property
    def descriptor_index(self) -> int:
        return self.w_value & 0xFF

    def __str__(self) -> str:
        return (
            f"bmRequestType=0x{self.bm_request_type:02x} bRequest=0x{self.b_request:02x} "
            f"wValue=0x{self.w_value:04x} wIndex=0x{self.w_index:04x} wLength={self.w_length}"
        )


@dataclass
class UsbEvent:
    """One capture record: a single submit or complete observation."""

    t: float
    bus: int
    device: int
    endpoint: int  # endpoint number only (0-15), direction stripped off
    direction_in: bool
    transfer_type: int  # a TransferType value (or an unrecognised raw int)
    is_submit: bool
    status: int
    xfer_id: int
    data: bytes = b""
    setup: Optional[SetupPacket] = None
    source: str = "usbpcap"

    @property
    def is_complete(self) -> bool:
        return not self.is_submit


@dataclass
class Transfer:
    """A submit+complete pair (or a lone half, if its partner wasn't captured)
    merged into a single logical USB transfer."""

    bus: int
    device: int
    endpoint: int
    direction_in: bool
    transfer_type: int
    xfer_id: int
    t_submit: Optional[float]
    t_complete: Optional[float]
    status: int
    data: bytes
    setup: Optional[SetupPacket] = None

    @property
    def t(self) -> float:
        """Representative timestamp: submit time if known, else complete time."""
        return self.t_submit if self.t_submit is not None else self.t_complete  # type: ignore[return-value]

    @property
    def duration(self) -> Optional[float]:
        if self.t_submit is not None and self.t_complete is not None:
            return self.t_complete - self.t_submit
        return None

    @property
    def endpoint_address(self) -> int:
        """The endpoint byte as it would appear in a descriptor (bit7 = IN)."""
        return self.endpoint | (0x80 if self.direction_in else 0x00)

    @property
    def type_name(self) -> str:
        return TransferType.label(self.transfer_type)

    @property
    def direction_name(self) -> str:
        return "IN" if self.direction_in else "OUT"


def pair_events(events: Iterable[UsbEvent]) -> list[Transfer]:
    """Group events sharing an ``xfer_id`` into :class:`Transfer` objects.

    Payload bytes are taken from whichever half of the pair matches the
    transfer's direction (completion for IN transfers -- the data arrives on
    the way back up; submission for OUT transfers -- the data goes down with
    the request), falling back to the other half if that one is empty. This
    also works for multi-stage control transfers (SETUP/DATA/STATUS stages
    sharing one id): the setup packet is taken from whichever stage carried
    one, and payload bytes from all matching-direction stages are
    concatenated in time order.

    Transfers are returned sorted by their representative timestamp.
    """
    groups: dict[int, list[UsbEvent]] = {}
    order: list[int] = []
    for ev in events:
        if ev.xfer_id not in groups:
            groups[ev.xfer_id] = []
            order.append(ev.xfer_id)
        groups[ev.xfer_id].append(ev)

    transfers: list[Transfer] = []
    for xid in order:
        evs = sorted(groups[xid], key=lambda e: e.t)
        base = evs[0]
        submits = [e for e in evs if e.is_submit]
        completes = [e for e in evs if e.is_complete]
        preferred, fallback = (completes, submits) if base.direction_in else (submits, completes)

        data = b"".join(e.data for e in preferred if e.data)
        if not data:
            data = b"".join(e.data for e in fallback if e.data)

        setup = next((e.setup for e in evs if e.setup is not None), None)
        status = completes[-1].status if completes else submits[-1].status if submits else 0

        transfers.append(
            Transfer(
                bus=base.bus,
                device=base.device,
                endpoint=base.endpoint,
                direction_in=base.direction_in,
                transfer_type=base.transfer_type,
                xfer_id=xid,
                t_submit=submits[0].t if submits else None,
                t_complete=completes[-1].t if completes else None,
                status=status,
                data=data,
                setup=setup,
            )
        )

    transfers.sort(key=lambda tr: tr.t)
    return transfers


def events_from_frames(frames: Iterable["Frame"]) -> list[UsbEvent]:
    """Parse captured link-layer frames into :class:`UsbEvent` records,
    dispatching on each frame's link type. Frames with an unrecognised link
    type, or that are too short/malformed to parse, are silently skipped
    (real captures can contain truncated frames e.g. due to a small
    snaplen)."""
    # Imported here (not at module level) to avoid a circular import: the
    # usbpcap/usbmon parsers import SetupPacket/UsbEvent from this module.
    from . import usbmon, usbpcap

    events: list[UsbEvent] = []
    for fr in frames:
        try:
            if fr.linktype == DLT_USBPCAP:
                events.append(usbpcap.parse_usbpcap_frame(fr.data, fr.t))
            elif fr.linktype in (DLT_USB_LINUX, DLT_USB_LINUX_MMAPPED):
                events.append(usbmon.parse_usbmon_frame(fr.data, fr.t, fr.linktype))
            # else: not a USB link type we understand -- skip.
        except (struct.error, ValueError, IndexError):
            continue
    return events
