"""Read-only, cross-platform discovery probe for the Thermo Nicolet Summit / Summit X.

This module NEVER sends any vendor-specific command to the spectrometer. It only:

  1. Enumerates USB devices attached to this computer (VID:PID, manufacturer/product
     strings, interface/endpoint descriptors) -- via `pyusb` if installed, otherwise
     by shelling out to a platform tool (`lsusb -v` on Linux, `system_profiler
     SPUSBDataType` on macOS, `Get-PnpDevice`/`pnputil` via PowerShell on Windows).
  2. Lists network interfaces and their addresses, to spot a USB-presented network
     adapter (RNDIS / CDC-ECM) that the instrument might expose -- via `psutil` if
     installed, otherwise a platform tool (`ip` on Linux, `ifconfig` on macOS,
     `ipconfig` on Windows).
  3. Flags anything in the above that looks Thermo/Nicolet/OMNIC/Summit related.
  4. Optionally (`--scan-host IP`) performs a *polite* TCP connect-scan of a short
     list of common/known ports against a single host you specify (e.g. an IP the
     instrument took on a USB-RNDIS or Ethernet link). This only attempts a TCP
     three-way handshake and immediately closes the socket -- it never transmits
     any payload.
  5. Writes everything collected to a single timestamped JSON report.

Run it with the instrument plugged in and powered on, ideally once *without* the
vendor's OMNIC Paradigm software running (to see the instrument's own idle state)
and again while OMNIC Paradigm is connected to it, then diff the two reports.

This module is designed to run unmodified inside a frozen (PyInstaller) GUI app on
a machine with no Python installed:

- it never raises out of :func:`run_probe` -- every failure is recorded in the
  report dict instead;
- every ``subprocess`` call has a timeout and, on Windows, passes
  ``creationflags=subprocess.CREATE_NO_WINDOW`` so a console window never flashes
  in front of a windowed GUI app;
- it degrades gracefully when ``pyusb``/``psutil`` are not installed/bundled.

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


# --------------------------------------------------------------------------
# 1. USB enumeration
# --------------------------------------------------------------------------


def usb_devices_via_pyusb() -> list[dict[str, Any]]:
    devices = []
    for dev in usb.core.find(find_all=True):  # type: ignore[union-attr]
        entry: dict[str, Any] = {
            "vendor_id": f"0x{dev.idVendor:04X}",
            "product_id": f"0x{dev.idProduct:04X}",
            "bus": getattr(dev, "bus", None),
            "address": getattr(dev, "address", None),
            "device_class": getattr(dev, "bDeviceClass", None),
            "device_subclass": getattr(dev, "bDeviceSubClass", None),
            "manufacturer": None,
            "product": None,
            "serial_number": None,
            "interfaces": [],
            "source": "pyusb",
        }
        # String descriptors require an open handle on some platforms/permissions;
        # never fatal if unavailable (e.g. no permission) -- just omit them.
        for attr, field in (
            ("iManufacturer", "manufacturer"),
            ("iProduct", "product"),
            ("iSerialNumber", "serial_number"),
        ):
            idx = getattr(dev, attr, 0)
            if idx:
                try:
                    entry[field] = usb.util.get_string(dev, idx)  # type: ignore[union-attr]
                except Exception:
                    pass
        try:
            for cfg in dev:
                for intf in cfg:
                    iface_info = {
                        "interface_number": intf.bInterfaceNumber,
                        "class": intf.bInterfaceClass,
                        "subclass": intf.bInterfaceSubClass,
                        "protocol": intf.bInterfaceProtocol,
                        "endpoints": [
                            {
                                "address": f"0x{ep.bEndpointAddress:02X}",
                                "attributes": ep.bmAttributes,
                                "max_packet_size": ep.wMaxPacketSize,
                            }
                            for ep in intf
                        ],
                    }
                    entry["interfaces"].append(iface_info)
        except Exception as exc:
            entry["interfaces_error"] = str(exc)
        devices.append(entry)
    return devices


def usb_devices_via_lsusb() -> dict[str, Any]:
    rc, out, err = _run(["lsusb", "-v"], timeout=30)
    if rc == 127:
        rc2, out2, err2 = _run(["lsusb"])
        return {"source": "lsusb (basic, -v unavailable or failed)", "returncode": rc2,
                "stdout": out2, "stderr": err2}
    return {"source": "lsusb -v", "returncode": rc, "stdout": out, "stderr": err}


def usb_devices_via_system_profiler() -> dict[str, Any]:
    rc, out, err = _run(["system_profiler", "SPUSBDataType"], timeout=30)
    return {"source": "system_profiler SPUSBDataType", "returncode": rc,
            "stdout": out, "stderr": err}


def usb_devices_via_windows() -> dict[str, Any]:
    # Get-PnpDevice gives class/status; pnputil /enum-devices gives IDs including
    # VID_xxxx&PID_xxxx for USB devices. Both are read-only enumeration commands.
    ps_cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "Get-PnpDevice -PresentOnly | Select-Object Class,FriendlyName,InstanceId "
        "| Format-List | Out-String -Width 500",
    ]
    rc, out, err = _run(ps_cmd, timeout=30)
    result: dict[str, Any] = {"source": "Get-PnpDevice", "returncode": rc,
                               "stdout": out, "stderr": err}
    rc2, out2, err2 = _run(["pnputil", "/enum-devices", "/connected"], timeout=30)
    result["pnputil"] = {"returncode": rc2, "stdout": out2, "stderr": err2}
    return result


def collect_usb_info() -> dict[str, Any]:
    system = platform.system()
    result: dict[str, Any] = {"platform": system, "pyusb_available": HAVE_PYUSB}

    if HAVE_PYUSB:
        try:
            result["devices"] = usb_devices_via_pyusb()
            result["method"] = "pyusb"
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


def highlight_usb(usb_info: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    for dev in usb_info.get("devices", []) or []:
        vid_str = dev.get("vendor_id", "")
        try:
            vid = int(vid_str, 16) if vid_str else None
        except ValueError:
            vid = None
        strings = " ".join(
            str(dev.get(k) or "") for k in ("manufacturer", "product", "serial_number")
        )
        if vid is not None and vid in KNOWN_THERMO_USB_VIDS:
            hits.append(
                f"USB device {dev.get('vendor_id')}:{dev.get('product_id')} matches "
                f"known Thermo VID -- {KNOWN_THERMO_USB_VIDS[vid]}"
            )
        if _contains_interesting(strings):
            hits.append(
                f"USB device {dev.get('vendor_id')}:{dev.get('product_id')} strings "
                f"look Thermo/Nicolet-related: manufacturer={dev.get('manufacturer')!r} "
                f"product={dev.get('product')!r}"
            )
    fb = usb_info.get("fallback")
    if isinstance(fb, dict):
        blob = (fb.get("stdout") or "") + (fb.get("stderr") or "")
        if _contains_interesting(blob):
            hits.append(
                "Fallback USB listing output contains a Thermo/Nicolet/OMNIC-related "
                "string -- inspect the raw 'usb.fallback.stdout' field in the report."
            )
        for vid_hex in (f"{v:04x}" for v in KNOWN_THERMO_USB_VIDS):
            if vid_hex in blob.lower():
                hits.append(
                    f"Fallback USB listing output contains known Thermo VID {vid_hex} "
                    "-- inspect the raw output for the matching device."
                )
    return hits


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
          f"(pyusb available: {HAVE_PYUSB})")
    n_devices = len(usb_info.get("devices", []) or [])
    if usb_info.get("method") == "pyusb":
        print(f"  {n_devices} USB device(s) found via pyusb")
    print(f"Network enumeration method: {net_info.get('method')} "
          f"(psutil available: {HAVE_PSUTIL})")
    print()
    usb_highlights = report.get("usb_highlights") or []
    if usb_highlights:
        print("USB highlights (possible Thermo/Nicolet matches):")
        for h in usb_highlights:
            print(f"  - {h}")
    else:
        print("No Thermo/Nicolet-looking USB strings or known VIDs found.")
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
