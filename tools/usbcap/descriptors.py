"""Standard USB descriptor parsing and device identification.

These are the generic USB 2.0 descriptors (device/configuration/interface/
endpoint) -- publicly specified, not anything instrument-specific -- recovered
from GET_DESCRIPTOR control transfer responses seen in a capture, so a
capture can be identified/filtered by VID:PID and its interface/endpoint
layout displayed without needing the device plugged in.

NB: the ``bmAttributes`` transfer-type bits in an endpoint descriptor use a
DIFFERENT numbering from the USBPcap/usbmon capture-side ``TransferType``
(see :mod:`tools.usbcap.model`): 0=control, 1=isochronous, 2=bulk,
3=interrupt here, vs 0=isochronous, 1=interrupt, 2=control, 3=bulk there.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from .model import Transfer

DESCRIPTOR_TYPE_DEVICE = 1
DESCRIPTOR_TYPE_CONFIGURATION = 2
DESCRIPTOR_TYPE_INTERFACE = 4
DESCRIPTOR_TYPE_ENDPOINT = 5

_REQUEST_GET_DESCRIPTOR = 0x06

_ENDPOINT_XFER_NAMES = {0: "control", 1: "isochronous", 2: "bulk", 3: "interrupt"}


@dataclass
class EndpointDescriptor:
    address: int
    attributes: int
    max_packet_size: int
    interval: int

    @property
    def number(self) -> int:
        return self.address & 0x0F

    @property
    def direction_in(self) -> bool:
        return bool(self.address & 0x80)

    @property
    def transfer_type_name(self) -> str:
        return _ENDPOINT_XFER_NAMES.get(self.attributes & 0x03, "unknown")

    def __str__(self) -> str:
        dirn = "IN" if self.direction_in else "OUT"
        return (
            f"EP{self.number} {dirn:3s} {self.transfer_type_name:10s} "
            f"wMaxPacketSize={self.max_packet_size} bInterval={self.interval}"
        )


@dataclass
class InterfaceDescriptor:
    number: int
    alternate: int
    interface_class: int
    interface_subclass: int
    interface_protocol: int
    endpoints: list[EndpointDescriptor] = field(default_factory=list)


@dataclass
class ConfigDescriptor:
    value: int
    num_interfaces: int
    attributes: int
    max_power: int
    interfaces: list[InterfaceDescriptor] = field(default_factory=list)


@dataclass
class DeviceDescriptor:
    bcd_usb: int
    device_class: int
    device_subclass: int
    device_protocol: int
    max_packet_size0: int
    vendor_id: int
    product_id: int
    bcd_device: int
    num_configurations: int


@dataclass
class DeviceInfo:
    bus: int
    device: int
    device_descriptor: Optional[DeviceDescriptor] = None
    config_descriptor: Optional[ConfigDescriptor] = None

    @property
    def vid_pid(self) -> Optional[tuple[int, int]]:
        if self.device_descriptor is None:
            return None
        return (self.device_descriptor.vendor_id, self.device_descriptor.product_id)


def parse_device_descriptor(data: bytes) -> DeviceDescriptor:
    if len(data) < 18:
        raise ValueError(f"device descriptor too short: {len(data)} bytes")
    (
        b_length,
        b_descriptor_type,
        bcd_usb,
        device_class,
        device_subclass,
        device_protocol,
        max_packet_size0,
        vendor_id,
        product_id,
        bcd_device,
        _i_manufacturer,
        _i_product,
        _i_serial,
        num_configurations,
    ) = struct.unpack_from("<BBHBBBBHHHBBBB", data, 0)
    if b_descriptor_type != DESCRIPTOR_TYPE_DEVICE:
        raise ValueError(f"not a device descriptor (bDescriptorType={b_descriptor_type})")
    return DeviceDescriptor(
        bcd_usb=bcd_usb,
        device_class=device_class,
        device_subclass=device_subclass,
        device_protocol=device_protocol,
        max_packet_size0=max_packet_size0,
        vendor_id=vendor_id,
        product_id=product_id,
        bcd_device=bcd_device,
        num_configurations=num_configurations,
    )


def parse_config_descriptor(data: bytes) -> ConfigDescriptor:
    """Parse a (possibly truncated) GET_DESCRIPTOR(CONFIGURATION) response:
    the 9-byte configuration descriptor followed by its interface/endpoint
    (and any class-specific) sub-descriptors, up to ``wTotalLength`` bytes."""
    if len(data) < 9:
        raise ValueError(f"configuration descriptor too short: {len(data)} bytes")
    (
        _b_length,
        b_descriptor_type,
        w_total_length,
        num_interfaces,
        config_value,
        _i_configuration,
        attributes,
        max_power,
    ) = struct.unpack_from("<BBHBBBBB", data, 0)
    if b_descriptor_type != DESCRIPTOR_TYPE_CONFIGURATION:
        raise ValueError(f"not a configuration descriptor (bDescriptorType={b_descriptor_type})")

    config = ConfigDescriptor(
        value=config_value,
        num_interfaces=num_interfaces,
        attributes=attributes,
        max_power=max_power,
    )

    end = min(len(data), w_total_length) if w_total_length else len(data)
    pos = 9
    current_iface: Optional[InterfaceDescriptor] = None
    while pos + 2 <= end:
        b_len = data[pos]
        b_type = data[pos + 1]
        if b_len < 2 or pos + b_len > end:
            break
        chunk = data[pos : pos + b_len]
        if b_type == DESCRIPTOR_TYPE_INTERFACE and b_len >= 9:
            (
                _bl,
                _bt,
                if_number,
                if_alt,
                _num_ep,
                if_class,
                if_subclass,
                if_proto,
                _i_interface,
            ) = struct.unpack_from("<BBBBBBBBB", chunk, 0)
            current_iface = InterfaceDescriptor(
                number=if_number,
                alternate=if_alt,
                interface_class=if_class,
                interface_subclass=if_subclass,
                interface_protocol=if_proto,
            )
            config.interfaces.append(current_iface)
        elif b_type == DESCRIPTOR_TYPE_ENDPOINT and b_len >= 7:
            _bl, _bt, ep_addr, ep_attr, ep_mps, ep_interval = struct.unpack_from(
                "<BBBBHB", chunk, 0
            )
            ep = EndpointDescriptor(
                address=ep_addr, attributes=ep_attr, max_packet_size=ep_mps, interval=ep_interval
            )
            if current_iface is not None:
                current_iface.endpoints.append(ep)
        # Other descriptor types (HID, class/vendor-specific, etc.) are
        # skipped but still advance pos by their own bLength.
        pos += b_len

    return config


def find_devices(transfers: list[Transfer]) -> dict[tuple[int, int], DeviceInfo]:
    """Scan GET_DESCRIPTOR(DEVICE)/GET_DESCRIPTOR(CONFIGURATION) responses in
    ``transfers`` and return a ``{(bus, device): DeviceInfo}`` map."""
    devices: dict[tuple[int, int], DeviceInfo] = {}
    for tr in transfers:
        if tr.setup is None or not tr.setup.direction_in:
            continue
        if tr.setup.b_request != _REQUEST_GET_DESCRIPTOR:
            continue
        key = (tr.bus, tr.device)
        desc_type = tr.setup.descriptor_type
        if desc_type == DESCRIPTOR_TYPE_DEVICE and len(tr.data) >= 18:
            try:
                dd = parse_device_descriptor(tr.data[:18])
            except ValueError:
                continue
            info = devices.setdefault(key, DeviceInfo(bus=tr.bus, device=tr.device))
            info.device_descriptor = dd
        elif desc_type == DESCRIPTOR_TYPE_CONFIGURATION and len(tr.data) >= 9:
            try:
                cd = parse_config_descriptor(tr.data)
            except ValueError:
                continue
            info = devices.setdefault(key, DeviceInfo(bus=tr.bus, device=tr.device))
            # Prefer the fullest configuration descriptor seen (the first
            # GET_DESCRIPTOR(CONFIGURATION) request typically only asks for
            # the 9-byte header to learn wTotalLength, then a second request
            # fetches the whole thing).
            if info.config_descriptor is None or len(cd.interfaces) >= len(
                info.config_descriptor.interfaces
            ):
                info.config_descriptor = cd
    return devices


def filter_transfers_by_vid_pid(
    transfers: list[Transfer],
    devices: dict[tuple[int, int], DeviceInfo],
    vid: int,
    pid: int,
) -> list[Transfer]:
    """Return only the transfers belonging to a (bus, device) identified as
    matching ``vid``:``pid``. If no device in the capture matches, returns
    ``transfers`` unchanged (nothing to filter out)."""
    addrs = {addr for addr, info in devices.items() if info.vid_pid == (vid, pid)}
    if not addrs:
        return transfers
    return [tr for tr in transfers if (tr.bus, tr.device) in addrs]
