"""USB transport for the Varian 3100 FT-IR (Excalibur series).

USB 1.x full speed, VID:PID 0614:0001: vendor control requests on endpoint
0 plus one bulk IN endpoint (0x81) for interferogram data. See
``docs/varian-protocol.md``.

:class:`UsbTransport` talks to the hardware through pyusb;
:class:`VarianTransport` is what the driver needs, so tests use
``varian.fake.FakeVarianTransport`` instead.
"""

from __future__ import annotations

import platform
from typing import Protocol, runtime_checkable

from openftir.instruments.base import InstrumentError

from .protocol import EP_DATA_IN, PRODUCT_ID, VENDOR_ID

__all__ = ["VarianTransport", "UsbTransport"]

_USB_INTERFACE = 0
_CTRL_OUT = 0x40   # vendor, host to device
_CTRL_IN = 0xC0    # vendor, device to host

_WINDOWS_DRIVER_HINT = (
    "OpenFTIR could not open the Varian. On Windows it needs the WinUSB "
    "driver: run Zadig, pick the device with USB ID 0614 0001, choose WinUSB "
    "and click Replace Driver (see docs/install.md). Close Resolutions Pro "
    "and any virtual machine using the Varian first: only one program can "
    "use it at a time."
)
_LINUX_PERMISSION_HINT = (
    "This usually means udev permissions aren't installed -- see "
    "packaging/linux/99-openftir.rules and docs/install.md."
)


@runtime_checkable
class VarianTransport(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def control_in(self, request: int, value: int, index: int, length: int) -> bytes: ...

    def control_out(self, request: int, value: int, index: int, data: bytes = b"") -> None: ...

    def bulk_read(self, size: int, timeout: float) -> bytes:
        """Read up to ``size`` bytes from EP 0x81; ``b""`` on timeout.
        May be called from a second thread while control requests run."""
        ...


def _get_libusb_backend():
    try:
        import libusb_package
    except Exception:
        return None
    try:
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


class UsbTransport:
    """pyusb-based :class:`VarianTransport`."""

    def __init__(self, platform_name: str | None = None) -> None:
        self._dev = None
        self._usb_core = None
        self._usb_util = None
        self._platform = platform_name or platform.system()

    def open(self) -> None:
        try:
            import usb.core as usb_core
            import usb.util as usb_util
        except Exception as exc:  # pragma: no cover - environment-dependent
            raise InstrumentError(f"pyusb is not installed/importable ({exc}).") from exc
        find_kwargs = {"idVendor": VENDOR_ID, "idProduct": PRODUCT_ID}
        backend = _get_libusb_backend()
        if backend is not None:
            find_kwargs["backend"] = backend
        try:
            dev = usb_core.find(**find_kwargs)
        except Exception as exc:
            raise InstrumentError(self._hint(f"failed to open the Varian: {exc}")) from exc
        if dev is None:
            raise InstrumentError(
                f"no Varian 3100 (USB {VENDOR_ID:04x}:{PRODUCT_ID:04x}) found. "
                + self._hint("Check it's plugged in and switched on.")
            )
        try:
            dev.set_configuration()
        except usb_core.USBError:
            pass  # already configured
        try:
            if self._platform == "Linux" and dev.is_kernel_driver_active(_USB_INTERFACE):
                dev.detach_kernel_driver(_USB_INTERFACE)
            usb_util.claim_interface(dev, _USB_INTERFACE)
        except usb_core.USBError as exc:
            raise InstrumentError(self._hint(f"failed to claim the Varian USB interface: {exc}")) from exc
        self._dev, self._usb_core, self._usb_util = dev, usb_core, usb_util

    def _hint(self, message: str) -> str:
        if self._platform == "Windows":
            return f"{message} {_WINDOWS_DRIVER_HINT}"
        if self._platform == "Linux":
            return f"{message} {_LINUX_PERMISSION_HINT}"
        return message

    def close(self) -> None:
        dev, usb_util = self._dev, self._usb_util
        self._dev = None
        if dev is not None and usb_util is not None:
            try:
                usb_util.dispose_resources(dev)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    def _require(self):
        if self._dev is None:
            raise InstrumentError("Varian transport is not open")
        return self._dev

    def control_in(self, request: int, value: int, index: int, length: int) -> bytes:
        dev = self._require()
        try:
            return bytes(dev.ctrl_transfer(_CTRL_IN, request, value, index, length, timeout=5000))
        except self._usb_core.USBError as exc:
            raise InstrumentError(f"USB error on request 0x{request:02X}: {exc}") from exc

    def control_out(self, request: int, value: int, index: int, data: bytes = b"") -> None:
        dev = self._require()
        try:
            dev.ctrl_transfer(_CTRL_OUT, request, value, index, data or None, timeout=5000)
        except self._usb_core.USBError as exc:
            raise InstrumentError(f"USB error on request 0x{request:02X}: {exc}") from exc

    def bulk_read(self, size: int, timeout: float) -> bytes:
        dev = self._require()
        try:
            return bytes(dev.read(EP_DATA_IN, size, timeout=int(timeout * 1000)))
        except self._usb_core.USBError as exc:
            timeout_error = getattr(self._usb_core, "USBTimeoutError", None)
            if (timeout_error is not None and isinstance(exc, timeout_error)) or getattr(exc, "errno", None) in (110, 60, 10060):
                return b""
            raise InstrumentError(f"USB read error on the data endpoint: {exc}") from exc
