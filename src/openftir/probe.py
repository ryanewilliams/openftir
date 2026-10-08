"""Read-only, cross-platform discovery probe for the Thermo Nicolet Summit / Summit X.

This module NEVER sends any vendor-specific command to the spectrometer. It only:

  1. Enumerates USB devices attached to this computer (VID:PID, manufacturer/product
     strings, interface/endpoint descriptors) -- via `pyusb` if installed, otherwise
     by shelling out to a platform tool (`lsusb -v` on Linux, `system_profiler
     SPUSBDataType` / `ioreg` on macOS, `Get-PnpDevice`/`Get-PnpDeviceProperty`/
     `pnputil` via PowerShell on Windows).
  2. Lists network interfaces and their addresses, to spot a USB-presented network
     adapter (RNDIS / CDC-ECM) that the instrument might expose -- via `psutil` if
     installed, otherwise a platform tool (`ip` on Linux, `ifconfig` on macOS,
     `ipconfig` on Windows).
  3. Flags anything in the above that looks Thermo/Nicolet/OMNIC/Summit related, and
     parses the raw fallback tool output into structured per-device records so a
     matching device is shown as a readable block instead of raw text the user has
     to dig through.
  4. Optionally (`--scan-host IP`) performs a *polite* TCP connect-scan of a short
     list of common/known ports against a single host you specify (e.g. an IP the
     instrument took on a USB-RNDIS or Ethernet link). This only attempts a TCP
     three-way handshake and immediately closes the socket -- it never transmits
     any payload.
  5. Writes everything collected to a single timestamped JSON report.

Run it with the instrument plugged in and powered on, ideally once *without* the
vendor's OMNIC Paradigm software running (to see the instrument's own idle state)
and again while OMNIC Paradigm is connected to it, then diff the two reports.

USB descriptor access (`pyusb`) is strictly read-only: this module only reads
cached device/configuration/interface/endpoint descriptors and string descriptors.
It never opens/claims an interface and never issues any control or bulk transfer
beyond what libusb performs implicitly to fetch those descriptors. Reading string
descriptors can fail where the device is bound to a vendor-specific kernel driver
rather than a generic driver (e.g. WinUSB) -- that failure is expected and recorded
rather than raised.

This module is designed to run unmodified inside a frozen (PyInstaller) GUI app on
a machine with no Python installed:

- it never raises out of :func:`run_probe` -- every failure is recorded in the
  report dict instead;
- every ``subprocess`` call has a timeout and, on Windows, passes
  ``creationflags=subprocess.CREATE_NO_WINDOW`` so a console window never flashes
  in front of a windowed GUI app;
- it degrades gracefully when ``pyusb``/``psutil`` are not installed/bundled.
- a USB backend is bundled via ``libusb-package`` (prebuilt libusb-1.0 shared
  libraries for Windows/macOS/Linux) so ``pyusb`` works in a frozen app even
  though the target machine has no system libusb installed. See
  :func:`_get_libusb1_backend`.

Usage (library):
    from openftir.probe import run_probe, write_report
    report = run_probe()
    path = write_report(report, "/tmp/reports")

Usage (CLI, same flags as the original standalone script):
    python -m openftir.probe
    python -m openftir.probe --scan-host 169.254.1.1
    python -m openftir.probe --output-dir /tmp/reports
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Optional dependencies -- the probe must still run with none of these.
# --------------------------------------------------------------------------
try:
    import usb.core  # type: ignore
    import usb.util  # type: ignore

    HAVE_PYUSB = True
except Exception:
    HAVE_PYUSB = False

try:
    import libusb_package  # type: ignore

    HAVE_LIBUSB_PACKAGE = True
except Exception:
    HAVE_LIBUSB_PACKAGE = False

try:
    import psutil  # type: ignore

    HAVE_PSUTIL = True
except Exception:
    HAVE_PSUTIL = False


# --------------------------------------------------------------------------
# Known / suspected identifiers -- see docs/instrument-research.md for sources.
# --------------------------------------------------------------------------

# USB vendor IDs publicly associated with Thermo Nicolet / Thermo Fisher Scientific
# instruments. 0x0EBB is independently confirmed by two public USB-ID databases
# (devicehunt.com and the-sz.com) as "Thermo Nicolet Corp." with product 0x0002
# "FT-IR Spectrometer". No public database entry could be confirmed for the Summit
# / Summit X specifically -- this list is a starting point for the live probe to
# check against, not a confirmed match for your instrument.
KNOWN_THERMO_USB_VIDS: dict[int, str] = {
    0x0EBB: "Thermo Nicolet Corp. (confirmed: PID 0x0002 = \"FT-IR Spectrometer\")",
}

# Case-insensitive substrings worth flagging anywhere in USB or network metadata.
INTERESTING_SUBSTRINGS = (
    "thermo",
    "nicolet",
    "omnic",
    "summit",
    "paradigm",
    "rndis",
    "remote ndis",
    "cdc ecm",
    "cdc-ecm",
    "usb ethernet",
)

# Ports documented by Thermo Fisher's own OMNIC Paradigm product-security guide and
# user guide as used between the OMNIC Paradigm client and its backend services
# (RabbitMQ/AMQP, a History Web API, a Workflow Web API, and UDP instrument
# discovery). On a "separate companion PC" deployment (Summit X / Summit LITE),
# Thermo's documentation states these all run *locally on the companion PC*, so
# finding them open on the instrument's own address would be a new, useful data
# point. On models with an embedded PC (Summit / Summit PRO / Summit OA) they are
# reachable over the network. See docs/instrument-research.md for citations.
COMMON_PORTS: list[int] = [
    21, 22, 23, 80, 443,          # generic / web UI
    139, 445,                     # SMB/NetBIOS (mentioned in Thermo's firewall docs)
    4369, 5671, 5672, 15672,      # RabbitMQ (EPMD, AMQP, AMQPS, management console)
    8000, 8080,                   # generic alt-HTTP
    9002,                         # OMNIC Paradigm "History WebAPI"
    9004,                         # OMNIC Paradigm "Web API Server"
    9090,                         # OMNIC Paradigm "Workflow Engine"
]

# Windows driver-binding property names surfaced (via Get-PnpDeviceProperty) for
# Thermo-matching devices only, mapped to the friendly key used in the report.
_WINDOWS_PROPERTY_KEYS: dict[str, str] = {
    "DEVPKEY_Device_Service": "service",
    "DEVPKEY_Device_DriverProvider": "driver_provider",
    "DEVPKEY_Device_DriverVersion": "driver_version",
    "DEVPKEY_Device_BusReportedDeviceDesc": "bus_reported_device_desc",
}

# Driver "service" names that indicate the device is bound to a generic USB
# driver libusb/pyusb can talk to. Anything else (e.g. a vendor's own kernel
# driver) means descriptor reads may work but opening the device/reading string
# descriptors will typically fail -- see `_windows_driver_note`.
_GENERIC_USB_SERVICES = {"winusb"}

#: Passed to subprocess.run on Windows so a console window never flashes in front
#: of a windowed (GUI) frozen app. 0 on other platforms (the flag only exists on
#: Windows), so it is always safe to pass.
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


def _contains_interesting(text: str) -> bool:
    low = text.lower()
    return any(s in low for s in INTERESTING_SUBSTRINGS)


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a subprocess, returning (returncode, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, "", f"error running {cmd!r}: {exc}"


def _normalize_hex_id(value: str | None) -> str | None:
    """Normalize a hex VID/PID-like token (e.g. "0x0ebb (Thermo Nicolet Corp.)"
    or "0EBB") to the canonical "0xXXXX" form used throughout this module."""
    if not value:
        return None
    token = value.strip().split()[0]
    token = token[2:] if token.lower().startswith("0x") else token
    try:
        return f"0x{int(token, 16):04X}"
    except ValueError:
        return value.strip()


def is_thermo_device(dev: dict[str, Any]) -> bool:
    """True if a (pyusb- or platform-tool-sourced) device record's VID or any of
    its name-like string fields look like the Thermo instrument."""
    vid = dev.get("vendor_id")
    if isinstance(vid, str):
        try:
            if int(vid, 16) in KNOWN_THERMO_USB_VIDS:
                return True
        except ValueError:
            pass
    strings = " ".join(
        str(dev.get(k) or "")
        for k in (
            "friendly_name",
            "name",
            "manufacturer",
            "product",
            "serial_number",
            "bus_reported_device_desc",
        )
    )
    return _contains_interesting(strings)


def _windows_driver_note(properties: dict[str, Any] | None) -> str | None:
    """If a Windows device's bound driver service isn't a generic one libusb can
    open (e.g. WinUSB), return a note explaining why descriptor access may be
    incomplete; otherwise None."""
    if not properties:
        return None
    service = properties.get("service")
    if not service:
        return None
    if service.strip().lower() in _GENERIC_USB_SERVICES:
        return None
    return (
        f"Bound to vendor driver {service!r}; OpenFTIR cannot access it on "
        "Windows while this driver is active (see docs)."
    )


# --------------------------------------------------------------------------
# 1. USB enumeration
# --------------------------------------------------------------------------


def _get_libusb1_backend():
    """Return a pyusb libusb-1.0 backend bundled via ``libusb-package`` if that
    package is installed, else None (pyusb falls back to its own discovery of a
    system-installed libusb, which a frozen app usually doesn't have)."""
    if not HAVE_LIBUSB_PACKAGE:
        return None
    try:
        return libusb_package.get_libusb1_backend()  # type: ignore[union-attr]
    except Exception:
        return None


def _bcd_to_str(bcd: int | None) -> str | None:
    if bcd is None:
        return None
    try:
        return f"{(bcd >> 8) & 0xFF}.{bcd & 0xFF:02X}"
    except TypeError:
        return None


def usb_devices_via_pyusb() -> list[dict[str, Any]]:
    backend = _get_libusb1_backend()
    find_kwargs: dict[str, Any] = {"find_all": True}
    if backend is not None:
        find_kwargs["backend"] = backend

    devices = []
    for dev in usb.core.find(**find_kwargs):  # type: ignore[union-attr]
        entry: dict[str, Any] = {
            "vendor_id": f"0x{dev.idVendor:04X}",
            "product_id": f"0x{dev.idProduct:04X}",
            "bus": getattr(dev, "bus", None),
            "address": getattr(dev, "address", None),
            "device_class": getattr(dev, "bDeviceClass", None),
            "device_subclass": getattr(dev, "bDeviceSubClass", None),
            "device_protocol": getattr(dev, "bDeviceProtocol", None),
            "usb_version": _bcd_to_str(getattr(dev, "bcdUSB", None)),
            "device_version": _bcd_to_str(getattr(dev, "bcdDevice", None)),
            "max_packet_size0": getattr(dev, "bMaxPacketSize0", None),
            "num_configurations": getattr(dev, "bNumConfigurations", None),
            "manufacturer": None,
            "product": None,
            "serial_number": None,
            "configurations": [],
            "string_descriptor_errors": {},
            "source": "pyusb",
            "backend": "libusb-package" if backend is not None else "pyusb default",
        }
        # String descriptors require reading from the device and can fail (e.g. no
        # permission, or on Windows when the device isn't bound to WinUSB) --
        # never fatal, just record the error.
        for attr, field in (
            ("iManufacturer", "manufacturer"),
            ("iProduct", "product"),
            ("iSerialNumber", "serial_number"),
        ):
            idx = getattr(dev, attr, 0)
            if idx:
                try:
                    entry[field] = usb.util.get_string(dev, idx)  # type: ignore[union-attr]
                except Exception as exc:
                    entry["string_descriptor_errors"][field] = str(exc)

        try:
            for cfg in dev:
                cfg_info: dict[str, Any] = {
                    "configuration_value": getattr(cfg, "bConfigurationValue", None),
                    "attributes": getattr(cfg, "bmAttributes", None),
                    "max_power_ma": (getattr(cfg, "bMaxPower", 0) or 0) * 2,
                    "interfaces": [],
                }
                for intf in cfg:
                    iface_info: dict[str, Any] = {
                        "interface_number": getattr(intf, "bInterfaceNumber", None),
                        "alternate_setting": getattr(intf, "bAlternateSetting", None),
                        "class": getattr(intf, "bInterfaceClass", None),
                        "subclass": getattr(intf, "bInterfaceSubClass", None),
                        "protocol": getattr(intf, "bInterfaceProtocol", None),
                        "endpoints": [],
                    }
                    for ep in intf:
                        address = getattr(ep, "bEndpointAddress", 0)
                        attributes = getattr(ep, "bmAttributes", 0)
                        try:
                            direction_str = (
                                "IN"
                                if usb.util.endpoint_direction(address) == usb.util.ENDPOINT_IN  # type: ignore[union-attr]
                                else "OUT"
                            )
                        except Exception:
                            direction_str = None
                        try:
                            transfer_type = usb.util.endpoint_type(attributes)  # type: ignore[union-attr]
                            transfer_type_str = {
                                usb.util.ENDPOINT_TYPE_CTRL: "control",  # type: ignore[union-attr]
                                usb.util.ENDPOINT_TYPE_ISO: "isochronous",  # type: ignore[union-attr]
                                usb.util.ENDPOINT_TYPE_BULK: "bulk",  # type: ignore[union-attr]
                                usb.util.ENDPOINT_TYPE_INTR: "interrupt",  # type: ignore[union-attr]
                            }.get(transfer_type, str(transfer_type))
                        except Exception:
                            transfer_type_str = None
                        iface_info["endpoints"].append(
                            {
                                "address": f"0x{address:02X}",
                                "direction": direction_str,
                                "transfer_type": transfer_type_str,
                                "attributes": attributes,
                                "max_packet_size": getattr(ep, "wMaxPacketSize", None),
                                "interval": getattr(ep, "bInterval", None),
                            }
                        )
                    cfg_info["interfaces"].append(iface_info)
                entry["configurations"].append(cfg_info)
        except Exception as exc:
            entry["configurations_error"] = str(exc)
        devices.append(entry)
    return devices


# -- Linux: lsusb ------------------------------------------------------------

_LSUSB_HEADER_RE = re.compile(
    r"^Bus\s+(?P<bus>\d+)\s+Device\s+(?P<device>\d+):\s+ID\s+"
    r"(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})\s*(?P<name>.*)$"
)


def parse_lsusb_output(text: str) -> list[dict[str, Any]]:
    """Parse ``lsusb`` or ``lsusb -v`` stdout into per-device records. Each
    device's full text block (including the ``-v`` descriptor dump, if present)
    is kept verbatim under ``raw_block`` alongside the VID/PID/name parsed out
    of its header line."""
    lines = text.splitlines()
    headers = [(i, m) for i, line in enumerate(lines) if (m := _LSUSB_HEADER_RE.match(line))]
    devices = []
    for idx, (start, m) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        block = "\n".join(lines[start:end]).rstrip()
        devices.append(
            {
                "bus": m.group("bus"),
                "device": m.group("device"),
                "vendor_id": f"0x{m.group('vid').upper()}",
                "product_id": f"0x{m.group('pid').upper()}",
                "name": m.group("name").strip(),
                "raw_block": block,
            }
        )
    return devices


def usb_devices_via_lsusb() -> dict[str, Any]:
    rc, out, err = _run(["lsusb", "-v"], timeout=30)
    if rc == 127:
        rc2, out2, err2 = _run(["lsusb"])
        return {
            "source": "lsusb (basic, -v unavailable or failed)",
            "returncode": rc2,
            "stdout": out2,
            "stderr": err2,
            "devices": parse_lsusb_output(out2),
        }
    return {
        "source": "lsusb -v",
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "devices": parse_lsusb_output(out),
    }


# -- macOS: system_profiler + ioreg -----------------------------------------

_SP_NAME_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[^:]+):\s*$")
_SP_PROP_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[^:]+):\s+(?P<value>.+?)\s*$")


def parse_system_profiler_usb(text: str) -> list[dict[str, Any]]:
    """Parse ``system_profiler SPUSBDataType`` stdout (an indented "Name:" /
    "Key: Value" tree) into one record per leaf node that has a Vendor/Product
    ID -- i.e. an actual USB device, not a bus/hub/host-controller entry."""
    devices: list[dict[str, Any]] = []
    current_name: str | None = None
    current_props: dict[str, str] = {}

    def flush() -> None:
        nonlocal current_name, current_props
        if current_name is not None and ("Vendor ID" in current_props or "Product ID" in current_props):
            vendor_raw = current_props.get("Vendor ID")
            manufacturer = None
            if vendor_raw:
                m = re.search(r"\((.*)\)", vendor_raw)
                if m:
                    manufacturer = m.group(1).strip()
            devices.append(
                {
                    "name": current_name,
                    "vendor_id": _normalize_hex_id(vendor_raw),
                    "product_id": _normalize_hex_id(current_props.get("Product ID")),
                    "manufacturer": manufacturer or current_props.get("Manufacturer"),
                    "serial_number": current_props.get("Serial Number"),
                    "location_id": current_props.get("Location ID"),
                    "speed": current_props.get("Speed"),
                    "raw_properties": current_props,
                }
            )
        current_name = None
        current_props = {}

    for line in text.splitlines():
        if not line.strip():
            continue
        name_m = _SP_NAME_LINE_RE.match(line)
        if name_m:
            flush()
            current_name = name_m.group("name").strip()
            current_props = {}
            continue
        prop_m = _SP_PROP_LINE_RE.match(line)
        if prop_m and current_name is not None:
            current_props[prop_m.group("key").strip()] = prop_m.group("value").strip()
    flush()
    return devices


def _extract_ioreg_device_blocks(ioreg_text: str, name_hints: list[str]) -> list[str]:
    """Best-effort extraction of per-device text blocks from ``ioreg -l`` output
    (a "+-o Name@..." tree) whose block mentions one of ``name_hints``
    (case-insensitive substring)."""
    lines = ioreg_text.splitlines()
    starts: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        stripped = line.lstrip(" |")
        if stripped.startswith("+-o "):
            starts.append((i, len(line) - len(stripped)))

    hints_low = [h.lower() for h in name_hints if h]
    if not hints_low:
        return []

    blocks = []
    for idx, (start, indent) in enumerate(starts):
        end = len(lines)
        for next_start, next_indent in starts[idx + 1 :]:
            if next_indent <= indent:
                end = next_start
                break
        block = "\n".join(lines[start:end])
        if any(h in block.lower() for h in hints_low):
            blocks.append(block)
    return blocks


def usb_devices_via_system_profiler() -> dict[str, Any]:
    rc, out, err = _run(["system_profiler", "SPUSBDataType"], timeout=30)
    devices = parse_system_profiler_usb(out)
    result: dict[str, Any] = {
        "source": "system_profiler SPUSBDataType",
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "devices": devices,
    }

    thermo_matches = [d for d in devices if is_thermo_device(d)]
    if thermo_matches:
        rc2, out2, err2 = _run(["ioreg", "-p", "IOUSB", "-l", "-w0"], timeout=30)
        hints = [h for d in thermo_matches for h in (d.get("name"), d.get("manufacturer")) if h]
        matching_blocks = _extract_ioreg_device_blocks(out2, hints) if rc2 == 0 else []
        result["ioreg"] = {
            "source": "ioreg -p IOUSB -l -w0",
            "returncode": rc2,
            "stdout": out2,
            "stderr": err2,
            "matching_blocks": matching_blocks,
        }
        if matching_blocks:
            for dev in thermo_matches:
                dev["ioreg_blocks"] = matching_blocks
    return result


# -- Windows: Get-PnpDevice / Get-PnpDeviceProperty / pnputil ----------------


def parse_windows_pnp_devices(text: str) -> list[dict[str, Any]]:
    """Parse ``Get-PnpDevice ... | Format-List`` stdout (blank-line-separated
    ``Key : Value`` blocks) into per-device records, pulling VID/PID out of
    InstanceId (e.g. ``USB\\VID_0EBB&PID_000C\\<serial>``)."""
    devices = []
    for block in re.split(r"\n\s*\n", text.strip()):
        props: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            props[key.strip()] = value.strip()
        if not props:
            continue
        instance_id = props.get("InstanceId", "")
        vid_m = re.search(r"VID_([0-9A-Fa-f]{4})", instance_id)
        pid_m = re.search(r"PID_([0-9A-Fa-f]{4})", instance_id)
        devices.append(
            {
                "class": props.get("Class"),
                "status": props.get("Status"),
                "friendly_name": props.get("FriendlyName"),
                "instance_id": instance_id or None,
                "vendor_id": f"0x{vid_m.group(1).upper()}" if vid_m else None,
                "product_id": f"0x{pid_m.group(1).upper()}" if pid_m else None,
            }
        )
    return devices


def parse_windows_pnp_properties(text: str) -> dict[str, Any]:
    """Parse ``Get-PnpDeviceProperty ... | Select-Object KeyName,Data |
    Format-List`` stdout into a dict keyed by the friendly names in
    ``_WINDOWS_PROPERTY_KEYS`` (unrecognized KeyNames are kept verbatim)."""
    friendly: dict[str, Any] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        keyname = None
        data = None
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "KeyName":
                keyname = value
            elif key == "Data":
                data = value
        if keyname:
            friendly[_WINDOWS_PROPERTY_KEYS.get(keyname, keyname)] = data
    return friendly


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_pnp_property_command(instance_id: str) -> list[str]:
    key_list = ",".join(_WINDOWS_PROPERTY_KEYS)
    quoted_id = _ps_single_quote(instance_id)
    script = (
        f"Get-PnpDeviceProperty -InstanceId {quoted_id} -KeyName {key_list} "
        "| Select-Object KeyName,Data | Format-List | Out-String -Width 500"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def usb_devices_via_windows() -> dict[str, Any]:
    # Get-PnpDevice gives class/status/identity; Get-PnpDeviceProperty (run only
    # for Thermo-matching devices) gives the bound driver service/provider;
    # pnputil /enum-devices gives a second, independent listing. All read-only.
    ps_cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "Get-PnpDevice -PresentOnly | Select-Object Class,FriendlyName,InstanceId,Status "
        "| Format-List | Out-String -Width 500",
    ]
    rc, out, err = _run(ps_cmd, timeout=30)
    devices = parse_windows_pnp_devices(out)
    result: dict[str, Any] = {
        "source": "Get-PnpDevice",
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "devices": devices,
    }

    for dev in devices:
        if not is_thermo_device(dev):
            continue
        instance_id = dev.get("instance_id")
        if not instance_id:
            continue
        props_rc, props_out, props_err = _run(_windows_pnp_property_command(instance_id), timeout=15)
        dev["properties_raw"] = {"returncode": props_rc, "stdout": props_out, "stderr": props_err}
        if props_rc == 0:
            dev["properties"] = parse_windows_pnp_properties(props_out)

    rc2, out2, err2 = _run(["pnputil", "/enum-devices", "/connected"], timeout=30)
    result["pnputil"] = {"returncode": rc2, "stdout": out2, "stderr": err2}
    return result


# -- shared ------------------------------------------------------------------


def collect_usb_info() -> dict[str, Any]:
    system = platform.system()
    result: dict[str, Any] = {"platform": system, "pyusb_available": HAVE_PYUSB}

    if HAVE_PYUSB:
        try:
            result["devices"] = usb_devices_via_pyusb()
            result["method"] = "pyusb"
            result["backend"] = "libusb-package" if _get_libusb1_backend() is not None else "pyusb default"
            return result
        except Exception as exc:
            result["pyusb_error"] = str(exc)
            # fall through to platform tool fallback

    result["method"] = "platform-tool-fallback"
    try:
        if system == "Linux":
            result["fallback"] = usb_devices_via_lsusb()
        elif system == "Darwin":
            result["fallback"] = usb_devices_via_system_profiler()
        elif system == "Windows":
            result["fallback"] = usb_devices_via_windows()
        else:
            result["fallback"] = {"error": f"no fallback implemented for platform {system!r}"}
    except Exception as exc:  # pragma: no cover - defensive, must never raise
        result["fallback"] = {"error": f"fallback collection failed: {exc}"}
    return result


def collect_thermo_matches(usb_info: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every USB device record (pyusb- or platform-tool-sourced) that
    looks like the Thermo instrument, normalized so :func:`format_thermo_matches`
    can render it regardless of which platform/method produced it."""
    matches: list[dict[str, Any]] = []
    for dev in usb_info.get("devices", []) or []:
        if is_thermo_device(dev):
            match = dict(dev)
            match.setdefault("source", "pyusb")
            matches.append(match)

    fb = usb_info.get("fallback")
    if isinstance(fb, dict):
        for dev in fb.get("devices", []) or []:
            if is_thermo_device(dev):
                match = dict(dev)
                match.setdefault("source", fb.get("source", "platform-tool"))
                matches.append(match)
    return matches


def highlight_usb(usb_info: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    for dev in collect_thermo_matches(usb_info):
        label = dev.get("product") or dev.get("name") or dev.get("friendly_name") or ""
        hits.append(
            f"USB device {dev.get('vendor_id')}:{dev.get('product_id')} "
            f"(source: {dev.get('source')}) matches known Thermo identifiers"
            + (f": {label}" if label else "")
        )

    fb = usb_info.get("fallback")
    if isinstance(fb, dict):
        blob = (fb.get("stdout") or "") + (fb.get("stderr") or "")
        if not hits and _contains_interesting(blob):
            hits.append(
                "Fallback USB listing output contains a Thermo/Nicolet/OMNIC-related "
                "string -- inspect the raw 'usb.fallback.stdout' field in the report."
            )
        if not hits:
            for vid_hex in (f"{v:04x}" for v in KNOWN_THERMO_USB_VIDS):
                if vid_hex in blob.lower():
                    hits.append(
                        f"Fallback USB listing output contains known Thermo VID {vid_hex} "
                        "-- inspect the raw output for the matching device."
                    )
    return hits


def format_thermo_matches(matches: list[dict[str, Any]]) -> list[str]:
    """Render each entry from :func:`collect_thermo_matches` as a multi-line,
    human-readable block (device descriptor fields, matching Windows driver
    properties, pyusb configuration/interface/endpoint descriptors, ...),
    used by both the CLI summary and the GUI dialog."""
    blocks: list[str] = []
    for dev in matches:
        lines: list[str] = []
        name = (
            dev.get("product")
            or dev.get("name")
            or dev.get("friendly_name")
            or dev.get("bus_reported_device_desc")
            or "(unnamed device)"
        )
        lines.append(
            f"{dev.get('vendor_id', '?')}:{dev.get('product_id', '?')}  {name}  "
            f"[source: {dev.get('source', '?')}]"
        )

        for label, key in (
            ("Manufacturer", "manufacturer"),
            ("Serial number", "serial_number"),
            ("Class", "class"),
            ("Status", "status"),
            ("Instance ID", "instance_id"),
            ("Bus-reported description", "bus_reported_device_desc"),
            ("Location ID", "location_id"),
            ("Speed", "speed"),
        ):
            value = dev.get(key)
            if value:
                lines.append(f"  {label}: {value}")

        bus, addr = dev.get("bus"), dev.get("address") or dev.get("device")
        if bus is not None or addr is not None:
            lines.append(f"  Bus/Address: {bus}/{addr}")

        properties = dev.get("properties")
        if isinstance(properties, dict) and properties:
            lines.append("  Windows driver binding (Get-PnpDeviceProperty):")
            for label, key in (
                ("Service", "service"),
                ("Driver provider", "driver_provider"),
                ("Driver version", "driver_version"),
                ("Bus-reported description", "bus_reported_device_desc"),
            ):
                value = properties.get(key)
                if value:
                    lines.append(f"    {label}: {value}")
            note = _windows_driver_note(properties)
            if note:
                lines.append(f"  NOTE: {note}")

        for cfg in dev.get("configurations", []) or []:
            lines.append(
                f"  Configuration {cfg.get('configuration_value')}: "
                f"max power {cfg.get('max_power_ma')} mA"
            )
            for intf in cfg.get("interfaces", []) or []:
                lines.append(
                    f"    Interface {intf.get('interface_number')} "
                    f"alt {intf.get('alternate_setting')}: class={intf.get('class')} "
                    f"subclass={intf.get('subclass')} protocol={intf.get('protocol')}"
                )
                for ep in intf.get("endpoints", []) or []:
                    lines.append(
                        f"      Endpoint {ep.get('address')} {ep.get('direction')} "
                        f"{ep.get('transfer_type')}: "
                        f"max_packet_size={ep.get('max_packet_size')} interval={ep.get('interval')}"
                    )
        if dev.get("configurations_error"):
            lines.append(f"  (configuration descriptors unavailable: {dev['configurations_error']})")

        for field, err in (dev.get("string_descriptor_errors") or {}).items():
            lines.append(f"  ({field} string descriptor unavailable: {err})")

        if dev.get("ioreg_blocks"):
            lines.append(
                "  ioreg: matching block(s) found -- see raw "
                "'usb.fallback.ioreg.matching_blocks' in the report"
            )

        if dev.get("raw_block"):
            lines.append("  (raw lsusb -v block saved in the JSON report under 'raw_block')")

        blocks.append("\n".join(lines))
    return blocks


# --------------------------------------------------------------------------
# 2. Network interfaces
# --------------------------------------------------------------------------


def network_info_via_psutil() -> dict[str, Any]:
    addrs = psutil.net_if_addrs()  # type: ignore[union-attr]
    stats = psutil.net_if_stats()  # type: ignore[union-attr]
    out: dict[str, Any] = {"interfaces": {}}
    for name, addr_list in addrs.items():
        out["interfaces"][name] = {
            "addresses": [
                {
                    "family": str(a.family),
                    "address": a.address,
                    "netmask": a.netmask,
                }
                for a in addr_list
            ],
            "is_up": getattr(stats.get(name), "isup", None),
        }
    return out


def network_info_via_platform_tool() -> dict[str, Any]:
    system = platform.system()
    if system == "Linux":
        rc, out, err = _run(["ip", "-brief", "addr"])
        rc2, out2, err2 = _run(["ip", "link"])
        return {
            "source": "ip",
            "addr": {"returncode": rc, "stdout": out, "stderr": err},
            "link": {"returncode": rc2, "stdout": out2, "stderr": err2},
        }
    elif system == "Darwin":
        rc, out, err = _run(["ifconfig"])
        return {"source": "ifconfig", "returncode": rc, "stdout": out, "stderr": err}
    elif system == "Windows":
        rc, out, err = _run(["ipconfig", "/all"], timeout=30)
        return {"source": "ipconfig /all", "returncode": rc, "stdout": out, "stderr": err}
    return {"error": f"no fallback implemented for platform {system!r}"}


def collect_network_info() -> dict[str, Any]:
    result: dict[str, Any] = {"psutil_available": HAVE_PSUTIL}
    if HAVE_PSUTIL:
        try:
            result["method"] = "psutil"
            result.update(network_info_via_psutil())
            return result
        except Exception as exc:
            result["psutil_error"] = str(exc)
    result["method"] = "platform-tool-fallback"
    try:
        result["fallback"] = network_info_via_platform_tool()
    except Exception as exc:  # pragma: no cover - defensive, must never raise
        result["fallback"] = {"error": f"fallback collection failed: {exc}"}
    return result


def highlight_network(net_info: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    for name in net_info.get("interfaces", {}) or {}:
        if _contains_interesting(name):
            hits.append(f"Network interface name looks relevant: {name!r}")
    fb = net_info.get("fallback")
    if isinstance(fb, dict):
        for key in ("stdout", "stderr"):
            blob = fb.get(key) or ""
            if _contains_interesting(blob):
                hits.append(
                    f"Network fallback '{key}' output contains a RNDIS/CDC-ECM/"
                    "Thermo-related string -- inspect the raw output in the report."
                )
    return hits


# --------------------------------------------------------------------------
# 3. Optional polite TCP connect-scan of a single host
# --------------------------------------------------------------------------


def polite_tcp_scan(host: str, ports: list[int], timeout: float = 0.75,
                     delay: float = 0.05) -> list[dict[str, Any]]:
    """Attempt a TCP handshake on each port in turn; never sends any payload."""
    results = []
    for port in ports:
        entry: dict[str, Any] = {"port": port}
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        start = time.monotonic()
        try:
            rc = sock.connect_ex((host, port))
            entry["open"] = rc == 0
            entry["errno"] = rc
        except socket.gaierror as exc:
            entry["error"] = f"DNS/resolution error: {exc}"
            entry["open"] = False
        except OSError as exc:
            entry["error"] = str(exc)
            entry["open"] = False
        finally:
            entry["elapsed_s"] = round(time.monotonic() - start, 3)
            sock.close()
        results.append(entry)
        time.sleep(delay)  # be polite -- don't hammer the instrument's embedded PC
    return results


# --------------------------------------------------------------------------
# Library entry points
# --------------------------------------------------------------------------


def run_probe(scan_host: str | None = None, ports: list[int] | None = None) -> dict[str, Any]:
    """Collect USB/network info (and optionally probe one host's ports) and
    return the report as a plain ``dict``.

    This never raises: any failure encountered while collecting a section is
    recorded in that section of the report instead of propagating, so it is
    safe to call from a GUI thread/worker without a surrounding try/except.
    """
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "hostname": socket.gethostname(),
        },
        "optional_dependencies": {
            "pyusb": HAVE_PYUSB,
            "libusb_package": HAVE_LIBUSB_PACKAGE,
            "psutil": HAVE_PSUTIL,
        },
    }

    try:
        usb_info = collect_usb_info()
    except Exception as exc:  # pragma: no cover - defensive, must never raise
        usb_info = {"error": f"usb collection failed: {exc}"}
    report["usb"] = usb_info
    try:
        report["usb_highlights"] = highlight_usb(usb_info)
    except Exception as exc:  # pragma: no cover - defensive
        report["usb_highlights"] = []
        report["usb_highlights_error"] = str(exc)
    try:
        report["usb_thermo_matches"] = collect_thermo_matches(usb_info)
    except Exception as exc:  # pragma: no cover - defensive
        report["usb_thermo_matches"] = []
        report["usb_thermo_matches_error"] = str(exc)

    try:
        net_info = collect_network_info()
    except Exception as exc:  # pragma: no cover - defensive, must never raise
        net_info = {"error": f"network collection failed: {exc}"}
    report["network"] = net_info
    try:
        report["network_highlights"] = highlight_network(net_info)
    except Exception as exc:  # pragma: no cover - defensive
        report["network_highlights"] = []
        report["network_highlights_error"] = str(exc)

    if scan_host:
        scan_ports = ports or COMMON_PORTS
        try:
            scan_results = polite_tcp_scan(scan_host, scan_ports)
        except Exception as exc:  # pragma: no cover - defensive, must never raise
            scan_results = []
            report["tcp_scan_error"] = str(exc)
        report["tcp_scan"] = {
            "host": scan_host,
            "ports_checked": scan_ports,
            "results": scan_results,
        }

    return report


def write_report(report: dict[str, Any], output_dir: str | Path) -> Path:
    """Write ``report`` as timestamped JSON inside ``output_dir`` and return the path."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"instrument_probe_{timestamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    return out_path


def _print_summary(report: dict[str, Any], out_path: Path) -> None:
    usb_info = report.get("usb", {})
    net_info = report.get("network", {})
    print(f"Report written to: {out_path}")
    print()
    print(f"USB enumeration method: {usb_info.get('method')} "
          f"(pyusb available: {HAVE_PYUSB}, backend: {usb_info.get('backend', 'n/a')})")
    n_devices = len(usb_info.get("devices", []) or [])
    if usb_info.get("method") == "pyusb":
        print(f"  {n_devices} USB device(s) found via pyusb")
    print(f"Network enumeration method: {net_info.get('method')} "
          f"(psutil available: {HAVE_PSUTIL})")
    print()

    thermo_matches = report.get("usb_thermo_matches") or []
    if thermo_matches:
        print(f"Thermo/Nicolet instrument(s) found ({len(thermo_matches)}):")
        for block in format_thermo_matches(thermo_matches):
            for line in block.splitlines():
                print(f"  {line}")
            print()
    else:
        print("No Thermo/Nicolet USB device identified.")
        usb_highlights = report.get("usb_highlights") or []
        if usb_highlights:
            print("Weaker USB highlights (string matches only -- inspect the raw report):")
            for h in usb_highlights:
                print(f"  - {h}")
    print()

    network_highlights = report.get("network_highlights") or []
    if network_highlights:
        print("Network highlights (possible RNDIS/CDC-ECM/Thermo matches):")
        for h in network_highlights:
            print(f"  - {h}")
    else:
        print("No RNDIS/CDC-ECM/Thermo-looking network interfaces found.")
    tcp_scan = report.get("tcp_scan")
    if tcp_scan:
        print()
        print(f"TCP connect-scan of {tcp_scan['host']}:")
        for r in tcp_scan["results"]:
            state = "OPEN" if r.get("open") else "closed/filtered"
            print(f"  port {r['port']:>5}: {state}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only discovery probe for a USB/network-attached FTIR spectrometer "
            "(written for the Thermo Nicolet Summit / Summit X). Lists USB devices "
            "and network interfaces and writes a timestamped JSON report. Never "
            "sends any vendor-specific command to the instrument."
        ),
    )
    parser.add_argument(
        "--scan-host",
        metavar="IP",
        help=(
            "Optionally run a polite TCP connect-scan of a short list of common "
            "ports against this single host (e.g. an address the instrument took "
            "on a USB-RNDIS or Ethernet link). Only attempts a TCP handshake; "
            "sends no payload."
        ),
    )
    parser.add_argument(
        "--ports",
        metavar="PORT",
        type=int,
        nargs="+",
        default=None,
        help="Override the default list of ports used with --scan-host.",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=".",
        help="Directory to write the timestamped JSON report into (default: cwd).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary printed to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    report = run_probe(scan_host=args.scan_host, ports=args.ports)
    out_path = write_report(report, args.output_dir)

    if not args.quiet:
        _print_summary(report, out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
