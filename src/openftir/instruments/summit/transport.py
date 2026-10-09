"""USB transport for the Nicolet Summit.

See ``docs/summit-protocol.md`` ("Transport"): USB 2.0 high-speed,
VID:PID 0ebb:000c, one vendor-class interface with four bulk/interrupt
endpoints:

====== === ========== ===========================================
EP      IO  type       role
====== === ========== ===========================================
0x01    OUT bulk       commands (uint32 LE length prefix + ASCII XML)
0x82    IN  bulk       replies (bare XML, no length prefix)
0x83    IN  bulk       interferogram data stream
0x84    IN  interrupt  asynchronous ``<benchMsg>`` events
====== === ========== ===========================================

:class:`UsbTransport` is the only implementation that talks to real
hardware; :class:`SummitTransport` is the protocol it (and
``summit.fake.FakeSummitTransport``) implement, so the driver never imports
``pyusb`` directly and tests never need real hardware.
"""

from __future__ import annotations

import platform
import struct
import threading
import time
from typing import Protocol, runtime_checkable

from openftir.instruments.base import InstrumentError

__all__ = ["SummitTransport", "UsbTransport"]

VENDOR_ID = 0x0EBB
PRODUCT_ID = 0x000C

EP_CMD_OUT = 0x01
EP_REPLY_IN = 0x82
EP_DATA_IN = 0x83
EP_EVENT_IN = 0x84

_USB_INTERFACE = 0
_PACKET_SIZE = 512

_WINDOWS_DRIVER_HINT = (
    "The Summit is using Thermo's Windows driver (NicUsbWdf); OpenFTIR can "
    "talk to it directly on macOS/Linux. On Windows see docs/install.md "
    "(UsbDk / WinUSB)."
)
_LINUX_PERMISSION_HINT = (
    "This usually means udev permissions aren't installed -- see "
    "packaging/linux/99-openftir.rules and docs/install.md."
)


@runtime_checkable
class SummitTransport(Protocol):
    """What the driver needs from a transport (real USB, or a fake for tests)."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def command(self, xml: str, timeout: float = 5.0) -> str:
        """Send one ``<cmd>...</cmd>`` on EP 0x01 and return the matching
        ``<reply>...</reply>`` read from EP 0x82."""
        ...

    def read_data(self, size: int, timeout: float = 5.0) -> bytes:
        """Read up to ``size`` bytes from the EP 0x83 interferogram stream,
        or ``b""`` if nothing arrived within ``timeout``."""
        ...

    def read_event(self, timeout: float = 0.1) -> str | None:
        """Read one ``<benchMsg>...</benchMsg>`` from EP 0x84, or ``None`` on
        timeout (events are asynchronous; nothing may be pending)."""
        ...


def _get_libusb_backend():
    """A pyusb libusb-1.0 backend bundled via ``libusb-package``, if that
    optional dependency is installed; otherwise ``None`` (pyusb then falls
    back to discovering a system libusb itself)."""
    try:
        import libusb_package
    except Exception:
        return None
    try:
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


class UsbTransport:
    """pyusb-based :class:`SummitTransport` talking to real hardware."""

    def __init__(self, platform_name: str | None = None) -> None:
        self._dev = None
        self._usb_core = None
        self._usb_util = None
        self._lock = threading.Lock()
        self._platform = platform_name or platform.system()

    # -- open/close -------------------------------------------------------

    def open(self) -> None:
        try:
            import usb.core as usb_core
            import usb.util as usb_util
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise InstrumentError(
                f"pyusb is not installed/importable ({exc}). Install the "
                "'probe' extra: pip install -e '.[probe]'."
            ) from exc
        self._open_with(usb_core, usb_util)

    def _open_with(self, usb_core, usb_util) -> None:
        """The actual open logic, parametrised over the ``usb.core``/
        ``usb.util`` modules so tests can inject a fake pair without pyusb
        or real hardware."""
        backend = _get_libusb_backend()
        find_kwargs = {"idVendor": VENDOR_ID, "idProduct": PRODUCT_ID}
        if backend is not None:
            find_kwargs["backend"] = backend

        try:
            dev = usb_core.find(**find_kwargs)
        except Exception as exc:
            raise InstrumentError(self._open_error_message(str(exc))) from exc

        if dev is None:
            raise InstrumentError(
                f"no Summit (USB {VENDOR_ID:04x}:{PRODUCT_ID:04x}) found. "
                + self._not_found_hint()
            )

        try:
            dev.set_configuration()
        except usb_core.USBError:
            pass  # already configured -- not an error

        try:
            if self._platform == "Linux" and dev.is_kernel_driver_active(_USB_INTERFACE):
                dev.detach_kernel_driver(_USB_INTERFACE)
            usb_util.claim_interface(dev, _USB_INTERFACE)
        except usb_core.USBError as exc:
            raise InstrumentError(self._claim_error_message(str(exc))) from exc

        self._dev = dev
        self._usb_core = usb_core
        self._usb_util = usb_util

    def _not_found_hint(self) -> str:
        if self._platform == "Windows":
            return _WINDOWS_DRIVER_HINT
        if self._platform == "Linux":
            return "Check it's plugged in and powered on. " + _LINUX_PERMISSION_HINT
        return "Check it's plugged in and powered on."

    def _open_error_message(self, detail: str) -> str:
        if self._platform == "Windows":
            return f"{_WINDOWS_DRIVER_HINT} (libusb error: {detail})"
        return f"failed to open the Summit: {detail}"

    def _claim_error_message(self, detail: str) -> str:
        if self._platform == "Windows":
            return f"{_WINDOWS_DRIVER_HINT} (libusb error: {detail})"
        if self._platform == "Linux":
            return f"failed to claim the Summit USB interface ({detail}). {_LINUX_PERMISSION_HINT}"
        return f"failed to claim the Summit USB interface: {detail}"

    def close(self) -> None:
        dev, usb_util = self._dev, self._usb_util
        self._dev = None
        if dev is not None and usb_util is not None:
            try:
                usb_util.dispose_resources(dev)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    # -- I/O ----------------------------------------------------------------

    def command(self, xml: str, timeout: float = 5.0) -> str:
        if self._dev is None:
            raise InstrumentError("Summit transport is not open")
        payload = xml.encode("ascii")
        header = struct.pack("<I", len(payload))
        with self._lock:
            try:
                self._dev.write(EP_CMD_OUT, header + payload, timeout=int(timeout * 1000))
            except self._usb_core.USBError as exc:
                raise InstrumentError(f"USB write error sending command: {exc}") from exc

            buf = bytearray()
            deadline = time.monotonic() + timeout
            while b"</reply>" not in buf and b"</benchMsg>" not in buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InstrumentError("timed out waiting for a Summit reply")
                try:
                    chunk = self._dev.read(
                        EP_REPLY_IN, _PACKET_SIZE, timeout=int(max(remaining, 0.01) * 1000)
                    )
                except self._usb_core.USBError as exc:
                    raise InstrumentError(f"USB read error on the reply endpoint: {exc}") from exc
                buf.extend(bytes(chunk))
            return buf.decode("ascii", errors="replace")

    def read_data(self, size: int, timeout: float = 5.0) -> bytes:
        if self._dev is None:
            raise InstrumentError("Summit transport is not open")
        try:
            chunk = self._dev.read(EP_DATA_IN, size, timeout=int(timeout * 1000))
        except self._usb_core.USBError as exc:
            # A timeout just means no data yet (e.g. between scans); the
            # driver decides how long silence is acceptable.
            timeout_error = getattr(self._usb_core, "USBTimeoutError", None)
            if (timeout_error is not None and isinstance(exc, timeout_error)) or getattr(exc, "errno", None) in (110, 60, 10060):
                return b""
            raise InstrumentError(f"USB read error on the data endpoint: {exc}") from exc
        return bytes(chunk)

    def read_event(self, timeout: float = 0.1) -> str | None:
        if self._dev is None:
            raise InstrumentError("Summit transport is not open")
        try:
            chunk = self._dev.read(EP_EVENT_IN, _PACKET_SIZE, timeout=int(timeout * 1000))
        except self._usb_core.USBError:
            return None
        return bytes(chunk).decode("ascii", errors="replace")
