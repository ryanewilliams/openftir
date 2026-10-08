"""Tests for the read-only instrument discovery probe (``openftir.probe``),
the ``openftir probe`` CLI subcommand, and the GUI ``ProbeDialog``."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

from openftir import probe as probe_mod
from openftir.cli import main as cli_main
from openftir.probe import (
    collect_thermo_matches,
    format_thermo_matches,
    is_thermo_device,
    parse_lsusb_output,
    parse_system_profiler_usb,
    parse_windows_pnp_devices,
    parse_windows_pnp_properties,
    run_probe,
    write_report,
)

# --------------------------------------------------------------------------
# Sample fallback-tool output fixtures. The Thermo device fields below (VID
# 0x0EBB, PID 0x000C, FriendlyName/Manufacturer/Service/DriverProvider/
# BusReportedDeviceDesc) come from a real Thermo Nicolet Summit X captured on
# a user's Windows laptop; the serial number is a fake placeholder.
# --------------------------------------------------------------------------

WINDOWS_PNP_DEVICE_SAMPLE = """
Class       : FT-IR Spectrometer Devices
FriendlyName: Thermo Fisher Scientific FT-IR Spectrometer
InstanceId  : USB\\VID_0EBB&PID_000C\\SN0000000
Status      : OK

Class       : Mouse
FriendlyName: HID-compliant mouse
InstanceId  : USB\\VID_046D&PID_C52B\\6&abc123&0&0000
Status      : OK
"""

WINDOWS_PNP_PROPERTY_SAMPLE = """
KeyName : DEVPKEY_Device_Service
Data    : NicUsbWdf

KeyName : DEVPKEY_Device_DriverProvider
Data    : Thermo Fisher Scientific

KeyName : DEVPKEY_Device_DriverVersion
Data    : 1.2.3.0

KeyName : DEVPKEY_Device_BusReportedDeviceDesc
Data    : Nicolet Summit
"""

MACOS_SYSTEM_PROFILER_SAMPLE = """
USB:

    USB 3.1 Bus:

      Host Controller Driver: AppleUSBXHCI

        FT-IR Spectrometer:

          Product ID: 0x000c
          Vendor ID: 0x0ebb  (Thermo Fisher Scientific)
          Version: 1.00
          Serial Number: SN0000000
          Speed: Up to 480 Mb/s
          Manufacturer: Thermo Fisher Scientific
          Location ID: 0x14100000 / 2

        USB Receiver:

          Product ID: 0xc52b
          Vendor ID: 0x046d  (Logitech Inc.)
          Version: 12.10
          Speed: Up to 12 Mb/s
          Manufacturer: Logitech
          Location ID: 0x14200000 / 3
"""

LSUSB_VERBOSE_SAMPLE = """Bus 001 Device 005: ID 0ebb:000c Thermo Fisher Scientific FT-IR Spectrometer
Device Descriptor:
  bLength                18
  bDescriptorType          1
  idVendor           0x0ebb Thermo Fisher Scientific
  idProduct          0x000c FT-IR Spectrometer
  iManufacturer            1 Thermo Fisher Scientific
  iProduct                 2 Nicolet Summit
Bus 001 Device 003: ID 046d:c52b Logitech, Inc. Unifying Receiver
Device Descriptor:
  bLength                18
  idVendor           0x046d Logitech, Inc.
  idProduct          0xc52b Unifying Receiver
"""


def test_run_probe_returns_usb_and_network_sections():
    report = run_probe()

    assert "usb" in report
    assert "network" in report
    assert "usb_highlights" in report
    assert "network_highlights" in report
    assert "host" in report
    assert "generated_at" in report
    assert "optional_dependencies" in report

    # On this Linux CI box pyusb/psutil may or may not be installed, but the
    # probe must still report a method and never raise.
    assert report["usb"].get("method") in ("pyusb", "platform-tool-fallback")
    assert report["network"].get("method") in ("psutil", "platform-tool-fallback")


def test_run_probe_never_raises_with_bad_scan_host():
    # A polite TCP scan against an unreachable/invalid host must not raise --
    # it should just record failures in the per-port results.
    report = run_probe(scan_host="256.256.256.256", ports=[9999])
    assert "tcp_scan" in report
    assert report["tcp_scan"]["host"] == "256.256.256.256"
    assert len(report["tcp_scan"]["results"]) == 1


def test_write_report_writes_valid_json(tmp_path):
    report = run_probe()
    out_path = write_report(report, tmp_path)

    assert out_path.exists()
    assert out_path.parent == tmp_path
    loaded = json.loads(out_path.read_text())
    assert loaded["host"]["hostname"] == report["host"]["hostname"]


def test_cli_probe_subcommand_writes_report(tmp_path, capsys):
    code = cli_main(["probe", "--output-dir", str(tmp_path), "--quiet"])
    assert code == 0

    files = list(tmp_path.glob("instrument_probe_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert "usb" in data
    assert "network" in data


def test_cli_probe_subcommand_prints_summary(tmp_path, capsys):
    code = cli_main(["probe", "--output-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Report written to:" in out


def test_probe_dialog_run_completes(qtbot):
    from openftir.gui.dialogs.probe_dialog import ProbeDialog

    dialog = ProbeDialog()
    qtbot.addWidget(dialog)

    dialog.run_btn.click()
    qtbot.waitUntil(lambda: dialog._report is not None, timeout=10000)

    assert dialog.save_btn.isEnabled()
    assert dialog.copy_btn.isEnabled()
    assert "Generated:" in dialog.summary_view.toPlainText()


def test_probe_dialog_save_report(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from openftir.gui.dialogs.probe_dialog import ProbeDialog

    dialog = ProbeDialog()
    qtbot.addWidget(dialog)

    dialog.run_btn.click()
    qtbot.waitUntil(lambda: dialog._report is not None, timeout=10000)

    save_path = tmp_path / "report.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(save_path), ""))
    )
    dialog.save_btn.click()

    assert save_path.exists()
    data = json.loads(save_path.read_text())
    assert "usb" in data


# --------------------------------------------------------------------------
# Fallback-tool output parsers
# --------------------------------------------------------------------------


def test_parse_windows_pnp_devices_extracts_vid_pid_and_matches():
    devices = parse_windows_pnp_devices(WINDOWS_PNP_DEVICE_SAMPLE)
    assert len(devices) == 2

    thermo = next(d for d in devices if d["vendor_id"] == "0x0EBB")
    assert thermo["product_id"] == "0x000C"
    assert thermo["friendly_name"] == "Thermo Fisher Scientific FT-IR Spectrometer"
    assert thermo["class"] == "FT-IR Spectrometer Devices"
    assert thermo["status"] == "OK"
    assert thermo["instance_id"] == "USB\\VID_0EBB&PID_000C\\SN0000000"
    assert is_thermo_device(thermo)

    mouse = next(d for d in devices if d["vendor_id"] == "0x046D")
    assert not is_thermo_device(mouse)


def test_parse_windows_pnp_properties():
    props = parse_windows_pnp_properties(WINDOWS_PNP_PROPERTY_SAMPLE)
    assert props == {
        "service": "NicUsbWdf",
        "driver_provider": "Thermo Fisher Scientific",
        "driver_version": "1.2.3.0",
        "bus_reported_device_desc": "Nicolet Summit",
    }


def test_windows_driver_note_warns_for_non_generic_service():
    props = parse_windows_pnp_properties(WINDOWS_PNP_PROPERTY_SAMPLE)
    note = probe_mod._windows_driver_note(props)
    assert note is not None
    assert "NicUsbWdf" in note
    assert "OpenFTIR cannot access it on Windows" in note

    assert probe_mod._windows_driver_note({"service": "WinUSB"}) is None
    assert probe_mod._windows_driver_note(None) is None


def test_parse_system_profiler_usb_extracts_leaf_devices_only():
    devices = parse_system_profiler_usb(MACOS_SYSTEM_PROFILER_SAMPLE)
    assert len(devices) == 2

    thermo = next(d for d in devices if d["vendor_id"] == "0x0EBB")
    assert thermo["product_id"] == "0x000C"
    assert thermo["name"] == "FT-IR Spectrometer"
    assert thermo["manufacturer"] == "Thermo Fisher Scientific"
    assert thermo["serial_number"] == "SN0000000"
    assert is_thermo_device(thermo)

    receiver = next(d for d in devices if d["vendor_id"] == "0x046D")
    assert not is_thermo_device(receiver)


def test_parse_lsusb_output_extracts_header_and_block():
    devices = parse_lsusb_output(LSUSB_VERBOSE_SAMPLE)
    assert len(devices) == 2

    thermo = next(d for d in devices if d["vendor_id"] == "0x0EBB")
    assert thermo["product_id"] == "0x000C"
    assert thermo["bus"] == "001"
    assert "Thermo Fisher Scientific FT-IR Spectrometer" in thermo["name"]
    assert "iProduct" in thermo["raw_block"]
    assert is_thermo_device(thermo)

    other = next(d for d in devices if d["vendor_id"] == "0x046D")
    assert not is_thermo_device(other)


def test_collect_thermo_matches_from_fallback_and_summary_blocks():
    windows_devices = parse_windows_pnp_devices(WINDOWS_PNP_DEVICE_SAMPLE)
    thermo = next(d for d in windows_devices if is_thermo_device(d))
    thermo["properties"] = parse_windows_pnp_properties(WINDOWS_PNP_PROPERTY_SAMPLE)

    usb_info = {
        "platform": "Windows",
        "pyusb_available": False,
        "method": "platform-tool-fallback",
        "fallback": {"source": "Get-PnpDevice", "devices": windows_devices},
    }
    matches = collect_thermo_matches(usb_info)
    assert len(matches) == 1
    assert matches[0]["source"] == "Get-PnpDevice"

    blocks = format_thermo_matches(matches)
    assert len(blocks) == 1
    block = blocks[0]
    assert "0x0EBB:0x000C" in block
    assert "Thermo Fisher Scientific FT-IR Spectrometer" in block
    assert "NicUsbWdf" in block
    assert "Thermo Fisher Scientific" in block
    assert "Nicolet Summit" in block
    assert "Bound to vendor driver 'NicUsbWdf'" in block
    assert "OpenFTIR cannot access it on Windows" in block


# --------------------------------------------------------------------------
# pyusb path, exercised with a fake Device so it runs without real hardware.
# --------------------------------------------------------------------------


class _FakeEndpoint:
    def __init__(self, address, attributes, max_packet_size, interval):
        self.bEndpointAddress = address
        self.bmAttributes = attributes
        self.wMaxPacketSize = max_packet_size
        self.bInterval = interval


class _FakeInterface:
    def __init__(self, number, alt, klass, subclass, protocol, endpoints):
        self.bInterfaceNumber = number
        self.bAlternateSetting = alt
        self.bInterfaceClass = klass
        self.bInterfaceSubClass = subclass
        self.bInterfaceProtocol = protocol
        self._endpoints = endpoints

    def __iter__(self):
        return iter(self._endpoints)


class _FakeConfig:
    def __init__(self, value, attributes, max_power, interfaces):
        self.bConfigurationValue = value
        self.bmAttributes = attributes
        self.bMaxPower = max_power
        self._interfaces = interfaces

    def __iter__(self):
        return iter(self._interfaces)


class _FakeDevice:
    def __init__(self, id_vendor, id_product, configs, strings):
        self.idVendor = id_vendor
        self.idProduct = id_product
        self.bus = 1
        self.address = 5
        self.bDeviceClass = 0
        self.bDeviceSubClass = 0
        self.bDeviceProtocol = 0
        self.bcdUSB = 0x0200
        self.bcdDevice = 0x0100
        self.bMaxPacketSize0 = 64
        self.bNumConfigurations = len(configs)
        self.iManufacturer = 1
        self.iProduct = 2
        self.iSerialNumber = 3
        self._configs = configs
        self._strings = strings

    def __iter__(self):
        return iter(self._configs)


def _make_fake_thermo_device() -> _FakeDevice:
    endpoint_in = _FakeEndpoint(address=0x81, attributes=0x02, max_packet_size=64, interval=0)
    endpoint_out = _FakeEndpoint(address=0x01, attributes=0x02, max_packet_size=64, interval=0)
    interface = _FakeInterface(
        number=0, alt=0, klass=0xFF, subclass=0, protocol=0, endpoints=[endpoint_in, endpoint_out]
    )
    config = _FakeConfig(value=1, attributes=0xA0, max_power=50, interfaces=[interface])
    return _FakeDevice(
        id_vendor=0x0EBB,
        id_product=0x000C,
        configs=[config],
        strings={
            1: "Thermo Fisher Scientific",
            2: "Nicolet Summit",
            3: "SN0000000",
        },
    )


@pytest.fixture
def fake_pyusb_thermo_device(monkeypatch):
    """Feed a fake pyusb Device into `usb_devices_via_pyusb` (no real hardware
    or backend needed) to check device/configuration/interface/endpoint
    descriptor parsing and the resulting Thermo summary block formatting."""
    if not probe_mod.HAVE_PYUSB:
        pytest.skip("pyusb not installed")
    fake_device = _make_fake_thermo_device()

    def fake_find(**kwargs):
        return iter([fake_device])

    def fake_get_string(dev, index):
        try:
            return dev._strings[index]
        except (AttributeError, KeyError):
            raise ValueError(f"no such string descriptor index {index}")

    monkeypatch.setattr(probe_mod.usb.core, "find", fake_find)
    monkeypatch.setattr(probe_mod.usb.util, "get_string", fake_get_string)
    return fake_device


def test_usb_devices_via_pyusb_parses_fake_device(fake_pyusb_thermo_device):
    devices = probe_mod.usb_devices_via_pyusb()
    assert len(devices) == 1
    dev = devices[0]

    assert dev["vendor_id"] == "0x0EBB"
    assert dev["product_id"] == "0x000C"
    assert dev["manufacturer"] == "Thermo Fisher Scientific"
    assert dev["product"] == "Nicolet Summit"
    assert dev["serial_number"] == "SN0000000"
    assert is_thermo_device(dev)

    [cfg] = dev["configurations"]
    assert cfg["configuration_value"] == 1
    assert cfg["max_power_ma"] == 100
    [iface] = cfg["interfaces"]
    assert iface["interface_number"] == 0
    assert len(iface["endpoints"]) == 2
    in_ep = next(e for e in iface["endpoints"] if e["address"] == "0x81")
    assert in_ep["direction"] == "IN"
    assert in_ep["transfer_type"] == "bulk"
    out_ep = next(e for e in iface["endpoints"] if e["address"] == "0x01")
    assert out_ep["direction"] == "OUT"


def test_usb_devices_via_pyusb_records_string_descriptor_errors(monkeypatch):
    if not probe_mod.HAVE_PYUSB:
        pytest.skip("pyusb not installed")
    fake_device = _make_fake_thermo_device()
    fake_device._strings = {}  # simulate a device bound to a vendor driver on
    # Windows, where string descriptors cannot be read without WinUSB.

    def fake_find(**kwargs):
        return iter([fake_device])

    def fake_get_string(dev, index):
        raise ValueError("could not read string descriptor (device not open)")

    monkeypatch.setattr(probe_mod.usb.core, "find", fake_find)
    monkeypatch.setattr(probe_mod.usb.util, "get_string", fake_get_string)

    devices = probe_mod.usb_devices_via_pyusb()
    [dev] = devices
    assert dev["manufacturer"] is None
    assert "manufacturer" in dev["string_descriptor_errors"]


def test_format_thermo_matches_renders_pyusb_descriptors(fake_pyusb_thermo_device):
    devices = probe_mod.usb_devices_via_pyusb()
    matches = collect_thermo_matches({"devices": devices})
    assert len(matches) == 1

    [block] = format_thermo_matches(matches)
    assert "0x0EBB:0x000C" in block
    assert "Nicolet Summit" in block
    assert "Configuration 1: max power 100 mA" in block
    assert "Interface 0 alt 0" in block
    assert "Endpoint 0x81 IN bulk" in block
    assert "Endpoint 0x01 OUT bulk" in block


# --------------------------------------------------------------------------
# End-to-end: run_probe/CLI/GUI summary show a full Thermo device block.
# --------------------------------------------------------------------------


def test_run_probe_summary_shows_full_thermo_block(monkeypatch, tmp_path, capsys):
    windows_devices = parse_windows_pnp_devices(WINDOWS_PNP_DEVICE_SAMPLE)
    for dev in windows_devices:
        if is_thermo_device(dev):
            dev["properties"] = parse_windows_pnp_properties(WINDOWS_PNP_PROPERTY_SAMPLE)

    def fake_collect_usb_info():
        return {
            "platform": "Windows",
            "pyusb_available": False,
            "method": "platform-tool-fallback",
            "fallback": {"source": "Get-PnpDevice", "devices": windows_devices},
        }

    monkeypatch.setattr(probe_mod, "collect_usb_info", fake_collect_usb_info)

    report = run_probe()
    out_path = write_report(report, tmp_path)
    probe_mod._print_summary(report, out_path)
    out = capsys.readouterr().out

    assert "Thermo Fisher Scientific FT-IR Spectrometer" in out
    assert "NicUsbWdf" in out
    assert "Bound to vendor driver 'NicUsbWdf'" in out


def test_probe_dialog_summary_shows_full_thermo_block(qtbot, monkeypatch):
    from openftir.gui.dialogs.probe_dialog import ProbeDialog

    windows_devices = parse_windows_pnp_devices(WINDOWS_PNP_DEVICE_SAMPLE)
    for dev in windows_devices:
        if is_thermo_device(dev):
            dev["properties"] = parse_windows_pnp_properties(WINDOWS_PNP_PROPERTY_SAMPLE)

    def fake_collect_usb_info():
        return {
            "platform": "Windows",
            "pyusb_available": False,
            "method": "platform-tool-fallback",
            "fallback": {"source": "Get-PnpDevice", "devices": windows_devices},
        }

    monkeypatch.setattr(probe_mod, "collect_usb_info", fake_collect_usb_info)

    dialog = ProbeDialog()
    qtbot.addWidget(dialog)
    dialog.run_btn.click()
    qtbot.waitUntil(lambda: dialog._report is not None, timeout=10000)

    text = dialog.summary_view.toPlainText()
    assert "Thermo Fisher Scientific FT-IR Spectrometer" in text
    assert "NicUsbWdf" in text
    assert "Bound to vendor driver 'NicUsbWdf'" in text
