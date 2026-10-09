"""Tests for ``openftir.instruments.summit.transport.UsbTransport``.

No real hardware or pyusb installation is exercised here: ``_open_with`` is
called directly with small fake ``usb.core``/``usb.util`` stand-ins so the
open/error-message logic can be tested on any machine (including one with
no Summit, or no USB at all).
"""

from __future__ import annotations

import struct

import pytest

from openftir.instruments.base import InstrumentError
from openftir.instruments.summit.transport import EP_CMD_OUT, UsbTransport


class _FakeUSBError(Exception):
    pass


class _FakeUsbCore:
    """Stands in for the ``usb.core`` module."""

    USBError = _FakeUSBError

    def __init__(self, dev=None, find_error=None):
        self._dev = dev
        self._find_error = find_error
        self.find_calls: list[dict] = []

    def find(self, **kwargs):
        self.find_calls.append(kwargs)
        if self._find_error is not None:
            raise self._find_error
        return self._dev


class _FakeUsbUtil:
    def __init__(self):
        self.claimed = []
        self.disposed = []

    def claim_interface(self, dev, interface):
        self.claimed.append((dev, interface))

    def dispose_resources(self, dev):
        self.disposed.append(dev)


class _FakeDevice:
    def __init__(self, kernel_driver_active=False):
        self._kernel_driver_active = kernel_driver_active
        self.detached = False
        self.configured = False
        self.writes: list[tuple[int, bytes]] = []
        self.read_queue: list[bytes] = []

    def set_configuration(self):
        self.configured = True

    def is_kernel_driver_active(self, interface):
        return self._kernel_driver_active

    def detach_kernel_driver(self, interface):
        self.detached = True

    def write(self, ep, data, timeout=None):
        self.writes.append((ep, bytes(data)))

    def read(self, ep, size, timeout=None):
        if not self.read_queue:
            raise _FakeUSBError("no data queued")
        return self.read_queue.pop(0)


# -- not found ----------------------------------------------------------------


def test_not_found_on_windows_mentions_nicusbwdf():
    transport = UsbTransport(platform_name="Windows")
    usb_core = _FakeUsbCore(dev=None)
    with pytest.raises(InstrumentError, match="NicUsbWdf"):
        transport._open_with(usb_core, _FakeUsbUtil())


def test_not_found_on_linux_mentions_udev_rules():
    transport = UsbTransport(platform_name="Linux")
    usb_core = _FakeUsbCore(dev=None)
    with pytest.raises(InstrumentError, match="99-openftir.rules"):
        transport._open_with(usb_core, _FakeUsbUtil())


def test_not_found_on_macos_generic_message():
    transport = UsbTransport(platform_name="Darwin")
    usb_core = _FakeUsbCore(dev=None)
    with pytest.raises(InstrumentError) as exc_info:
        transport._open_with(usb_core, _FakeUsbUtil())
    assert "NicUsbWdf" not in str(exc_info.value)


# -- find()/claim_interface() raising ------------------------------------------


def test_find_error_on_windows_mentions_nicusbwdf():
    transport = UsbTransport(platform_name="Windows")
    usb_core = _FakeUsbCore(find_error=_FakeUSBError("Access denied"))
    with pytest.raises(InstrumentError, match="NicUsbWdf"):
        transport._open_with(usb_core, _FakeUsbUtil())


def test_claim_interface_error_on_linux_mentions_udev_rules():
    dev = _FakeDevice()
    transport = UsbTransport(platform_name="Linux")
    usb_core = _FakeUsbCore(dev=dev)
    usb_util = _FakeUsbUtil()

    def _raise_claim(dev, interface):
        raise _FakeUSBError("Permission denied")

    usb_util.claim_interface = _raise_claim
    with pytest.raises(InstrumentError, match="99-openftir.rules"):
        transport._open_with(usb_core, usb_util)


def test_claim_interface_error_on_windows_mentions_nicusbwdf():
    dev = _FakeDevice()
    transport = UsbTransport(platform_name="Windows")
    usb_core = _FakeUsbCore(dev=dev)
    usb_util = _FakeUsbUtil()

    def _raise_claim(dev, interface):
        raise _FakeUSBError("could not open device")

    usb_util.claim_interface = _raise_claim
    with pytest.raises(InstrumentError, match="NicUsbWdf"):
        transport._open_with(usb_core, usb_util)


# -- successful open + kernel driver detach on Linux ---------------------------


def test_successful_open_claims_interface():
    dev = _FakeDevice()
    transport = UsbTransport(platform_name="Darwin")
    usb_core = _FakeUsbCore(dev=dev)
    usb_util = _FakeUsbUtil()
    transport._open_with(usb_core, usb_util)
    assert dev.configured
    assert usb_util.claimed == [(dev, 0)]
    assert not dev.detached  # not Linux -- never asked


def test_linux_detaches_active_kernel_driver():
    dev = _FakeDevice(kernel_driver_active=True)
    transport = UsbTransport(platform_name="Linux")
    usb_core = _FakeUsbCore(dev=dev)
    transport._open_with(usb_core, _FakeUsbUtil())
    assert dev.detached


def test_linux_does_not_detach_inactive_kernel_driver():
    dev = _FakeDevice(kernel_driver_active=False)
    transport = UsbTransport(platform_name="Linux")
    usb_core = _FakeUsbCore(dev=dev)
    transport._open_with(usb_core, _FakeUsbUtil())
    assert not dev.detached


# -- command()/read_data() wire format -----------------------------------------


def test_command_writes_length_prefixed_xml_and_reads_reply():
    dev = _FakeDevice()
    dev.read_queue = [b"<reply><a>1</a></reply>"]
    transport = UsbTransport(platform_name="Darwin")
    transport._open_with(_FakeUsbCore(dev=dev), _FakeUsbUtil())

    reply = transport.command("<cmd><get><a/></get></cmd>")
    assert reply == "<reply><a>1</a></reply>"

    ep, data = dev.writes[0]
    assert ep == EP_CMD_OUT
    length = struct.unpack_from("<I", data, 0)[0]
    assert data[4:] == b"<cmd><get><a/></get></cmd>"
    assert length == len(b"<cmd><get><a/></get></cmd>")


def test_command_concatenates_multiple_reply_packets():
    dev = _FakeDevice()
    dev.read_queue = [b"<reply><a>", b"1</a></reply>"]
    transport = UsbTransport(platform_name="Darwin")
    transport._open_with(_FakeUsbCore(dev=dev), _FakeUsbUtil())
    assert transport.command("<cmd><get><a/></get></cmd>") == "<reply><a>1</a></reply>"


def test_read_data_reads_from_ep83():
    dev = _FakeDevice()
    dev.read_queue = [b"\x01\x02\x03"]
    transport = UsbTransport(platform_name="Darwin")
    transport._open_with(_FakeUsbCore(dev=dev), _FakeUsbUtil())
    assert transport.read_data(512) == b"\x01\x02\x03"


def test_command_before_open_raises():
    transport = UsbTransport(platform_name="Darwin")
    with pytest.raises(InstrumentError, match="not open"):
        transport.command("<cmd><get><a/></get></cmd>")


def test_close_disposes_resources():
    dev = _FakeDevice()
    transport = UsbTransport(platform_name="Darwin")
    usb_util = _FakeUsbUtil()
    transport._open_with(_FakeUsbCore(dev=dev), usb_util)
    transport.close()
    assert usb_util.disposed == [dev]
    with pytest.raises(InstrumentError):
        transport.command("<cmd><get><a/></get></cmd>")
