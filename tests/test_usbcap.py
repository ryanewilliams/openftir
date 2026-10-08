"""Tests for tools/usbcap: the pure-Python USBPcap/usbmon capture reader,
parser, submit/complete pairing, descriptor parsing and CLI analysis
commands (summary/timeline/messages/diff/extract/guess-arrays).

All captures used here are synthetic (built with tools.usbcap.synth), not
real hardware traces -- no capture of the real instrument exists yet. The
Thermo VID:PID (0x0EBB:0x000C) and a believable descriptor/endpoint layout
are used only to exercise the VID:PID filter and descriptor pretty-printer.
"""

from __future__ import annotations

import itertools
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

# Allow running these tests from a source checkout without installing
# anything -- "tools" is a plain namespace package under the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.usbcap.analysis import (
    diff_captures,
    extract,
    guess_arrays_report,
    load_capture,
    messages_report,
    summarize,
    timeline,
)
from tools.usbcap.descriptors import parse_device_descriptor
from tools.usbcap.model import DLT_USBPCAP, TransferType
from tools.usbcap.pcap_reader import read_frames_bytes
from tools.usbcap.synth import build_idb, build_pcapng, build_shb, usbpcap_frame, usbpcap_setup_packet

THERMO_VID = 0x0EBB
THERMO_PID = 0x000C
DISTRACTOR_VID = 0x046D
DISTRACTOR_PID = 0xC52B

THERMO_BUS = 1
THERMO_DEV = 5
DISTRACTOR_BUS = 1
DISTRACTOR_DEV = 3

STATUS_CMD = bytes([0x20, 0x00, 0x00, 0x00])


def start_scan_cmd(scan_count: int) -> bytes:
    return bytes([0x10, 0x01, 0x00, 0x00, scan_count, 0x00, 0x00, 0x00])


def device_descriptor_bytes(vid: int, pid: int, bcd_device: int = 0x0100) -> bytes:
    return struct.pack(
        "<BBHBBBBHHHBBBB",
        18,
        1,
        0x0200,
        0,
        0,
        0,
        64,
        vid,
        pid,
        bcd_device,
        0,
        0,
        0,
        1,
    )


def config_descriptor_bytes() -> bytes:
    """One interface with EP1 OUT bulk, EP2 IN bulk, EP3 IN interrupt."""
    iface = struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 3, 0xFF, 0, 0, 0)
    ep1 = struct.pack("<BBBBHB", 7, 5, 0x01, 2, 512, 0)
    ep2 = struct.pack("<BBBBHB", 7, 5, 0x82, 2, 512, 0)
    ep3 = struct.pack("<BBBBHB", 7, 5, 0x83, 3, 8, 8)
    body = iface + ep1 + ep2 + ep3
    total_len = 9 + len(body)
    header = struct.pack("<BBHBBBBB", 9, 2, total_len, 1, 1, 0, 0x80, 50)
    return header + body


def synthetic_interferogram(n: int = 2048, center: int = 1024) -> np.ndarray:
    i = np.arange(n)
    tau = 40.0
    freq = 0.05
    amp = 5000.0
    rng = np.random.default_rng(0)
    x = amp * np.exp(-np.abs(i - center) / tau) * np.cos(2 * np.pi * freq * (i - center))
    x += rng.normal(scale=5.0, size=n)
    x += 1000.0  # DC baseline, as a real interferogram would have
    return x.astype(np.float32)


def _enum_frames(ids, bus: int, device: int, vid: int, pid: int, with_config: bool):
    """GET_DESCRIPTOR(DEVICE) [+ GET_DESCRIPTOR(CONFIGURATION)] submit/complete pairs."""
    frames = []
    t = 0.0
    setup_dev = usbpcap_setup_packet(0x80, 0x06, 0x0100, 0, 18)
    irp = next(ids)
    frames.append((usbpcap_frame(irp, bus, device, 0, True, 2, True, setup=setup_dev), t))
    frames.append(
        (
            usbpcap_frame(
                irp, bus, device, 0, True, 2, False, setup=setup_dev, data=device_descriptor_bytes(vid, pid)
            ),
            t + 0.001,
        )
    )
    t += 0.005
    if with_config:
        cfg = config_descriptor_bytes()
        setup_cfg = usbpcap_setup_packet(0x80, 0x06, 0x0200, 0, len(cfg))
        irp = next(ids)
        frames.append((usbpcap_frame(irp, bus, device, 0, True, 2, True, setup=setup_cfg), t))
        frames.append(
            (usbpcap_frame(irp, bus, device, 0, True, 2, False, setup=setup_cfg, data=cfg), t + 0.001)
        )
    return frames


def build_capture_frames(scan_count: int, n_samples: int = 2048, center: int = 1024, n_interrupts: int = 6):
    """Build the full synthetic session: distractor + Thermo enumeration,
    STATUS_CMD polling, one START_SCAN -> interferogram burst, and periodic
    interrupt polling. Returns (frames, interferogram_float32_array)."""
    ids = itertools.count(1)
    frames = []

    frames += _enum_frames(ids, DISTRACTOR_BUS, DISTRACTOR_DEV, DISTRACTOR_VID, DISTRACTOR_PID, with_config=False)
    frames += _enum_frames(ids, THERMO_BUS, THERMO_DEV, THERMO_VID, THERMO_PID, with_config=True)

    t = 0.05
    # A couple of status polls with no response, well before the scan.
    for _ in range(2):
        irp = next(ids)
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 1, False, 3, True, data=STATUS_CMD), t))
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 1, False, 3, False), t + 0.0005))
        t += 0.01

    # START_SCAN command.
    irp = next(ids)
    frames.append(
        (usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 1, False, 3, True, data=start_scan_cmd(scan_count)), t)
    )
    frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 1, False, 3, False), t + 0.0005))
    t += 0.002

    # Interferogram burst: many 512-byte IN packets on EP2, tightly spaced.
    interferogram = synthetic_interferogram(n_samples, center)
    raw = interferogram.tobytes()
    chunk_size = 512
    for off in range(0, len(raw), chunk_size):
        chunk = raw[off : off + chunk_size]
        irp = next(ids)
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 2, True, 3, True), t))
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 2, True, 3, False, data=chunk), t + 0.0002))
        t += 0.0005

    # Periodic interrupt polling (identical 1-byte "idle" payload every 0.5 s).
    t = 0.05
    for _ in range(n_interrupts):
        irp = next(ids)
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 3, True, 1, True), t))
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 3, True, 1, False, data=b"\x00"), t + 0.001))
        t += 0.5

    frames.sort(key=lambda fd: fd[1])
    return frames, interferogram


def write_capture(tmp_path: Path, name: str, frames, endian: str = "<") -> Path:
    pcapng = build_pcapng(frames, linktype=DLT_USBPCAP, endian=endian)
    path = tmp_path / name
    path.write_bytes(pcapng)
    return path


# --------------------------------------------------------------------------
# low-level reader/parser tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize("endian", ["<", ">"])
def test_pcapng_reader_roundtrips_both_endiannesses(tmp_path, endian):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames, endian=endian)
    read_back = list(read_frames_bytes(path.read_bytes()))
    assert len(read_back) == len(frames)
    assert all(fr.linktype == DLT_USBPCAP for fr in read_back)
    # Frames come back in the order written (we already wrote them sorted by time).
    for (expected_data, expected_t), fr in zip(frames, read_back):
        assert fr.data == expected_data
        assert fr.t == pytest.approx(expected_t, abs=1e-6)


def test_pcapng_reader_handles_multiple_interfaces():
    endian = "<"
    shb = build_shb(endian=endian)
    idb0 = build_idb(linktype=0, endian=endian)  # e.g. a non-USB interface
    idb1 = build_idb(linktype=DLT_USBPCAP, endian=endian)
    from tools.usbcap.synth import build_epb

    epb_if0 = build_epb(b"\x01\x02\x03\x04", t_seconds=0.0, endian=endian, interface_id=0)
    epb_if1 = build_epb(b"\x05\x06\x07\x08", t_seconds=1.0, endian=endian, interface_id=1)
    data = shb + idb0 + idb1 + epb_if0 + epb_if1

    frames = list(read_frames_bytes(data))
    assert len(frames) == 2
    assert frames[0].linktype == 0
    assert frames[0].data == b"\x01\x02\x03\x04"
    assert frames[1].linktype == DLT_USBPCAP
    assert frames[1].data == b"\x05\x06\x07\x08"
    assert frames[1].t == pytest.approx(1.0)


def test_classic_pcap_reader():
    # Minimal classic pcap: usec-resolution little-endian global header, one record.
    payload = usbpcap_frame(1, THERMO_BUS, THERMO_DEV, 1, False, 3, True, data=STATUS_CMD)
    global_header = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, DLT_USBPCAP)
    record_header = struct.pack("<IIII", 10, 250000, len(payload), len(payload))
    data = global_header + record_header + payload

    frames = list(read_frames_bytes(data))
    assert len(frames) == 1
    assert frames[0].linktype == DLT_USBPCAP
    assert frames[0].data == payload
    assert frames[0].t == pytest.approx(10.25)


def test_pairing_and_vid_pid_identification(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)

    # Distractor device's enumeration transfers must be filtered out.
    assert all((tr.bus, tr.device) == (THERMO_BUS, THERMO_DEV) for tr in cap.transfers)
    assert (DISTRACTOR_BUS, DISTRACTOR_DEV) in cap.all_devices
    assert cap.all_devices[(DISTRACTOR_BUS, DISTRACTOR_DEV)].vid_pid == (DISTRACTOR_VID, DISTRACTOR_PID)
    assert cap.all_devices[(THERMO_BUS, THERMO_DEV)].vid_pid == (THERMO_VID, THERMO_PID)

    # A GET_DESCRIPTOR(DEVICE) control transfer should be paired into one
    # Transfer carrying both the setup packet and the 18-byte response.
    ctrl = [tr for tr in cap.transfers if tr.transfer_type == TransferType.CONTROL and tr.setup is not None]
    assert ctrl
    dev_desc_transfer = next(tr for tr in ctrl if tr.setup.w_value == 0x0100)
    assert len(dev_desc_transfer.data) == 18
    dd = parse_device_descriptor(dev_desc_transfer.data)
    assert (dd.vendor_id, dd.product_id) == (THERMO_VID, THERMO_PID)


def test_config_descriptor_parsed_from_capture(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    info = cap.all_devices[(THERMO_BUS, THERMO_DEV)]
    assert info.config_descriptor is not None
    cfg = info.config_descriptor
    assert cfg.num_interfaces == 1
    assert len(cfg.interfaces) == 1
    eps = {ep.address: ep for ep in cfg.interfaces[0].endpoints}
    assert set(eps) == {0x01, 0x82, 0x83}
    assert eps[0x82].transfer_type_name == "bulk"
    assert eps[0x83].transfer_type_name == "interrupt"


# --------------------------------------------------------------------------
# CLI-level analysis tests
# --------------------------------------------------------------------------


def test_summary(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    text = summarize(cap)
    assert "0ebb:000c" in text
    assert "numInterfaces=1" in text
    assert "EP01" in text or "EP1" in text
    assert "duration" in text


def test_timeline_collapses_periodic_interrupts(tmp_path):
    frames, _ = build_capture_frames(scan_count=1, n_interrupts=6)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    text = timeline(cap, endpoint=0x83)
    assert "x6 identical" in text
    assert "every 0.500 s" in text


def test_timeline_endpoint_and_min_bytes_filters(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    text = timeline(cap, endpoint=0x82, min_bytes=100)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines  # the interferogram burst chunks all qualify
    assert all("EP02 IN" in ln for ln in lines)


def test_messages_clusters_out_commands(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)
    text = messages_report(cap)
    assert "prefix=20 00 00 00" in text  # STATUS_CMD
    assert "prefix=10 01 00 00" in text  # start_scan_cmd
    assert "0x82" in text  # typical response endpoint for START_SCAN
    assert "len=512" in text


def test_diff_detects_scan_count_byte(tmp_path):
    frames_a, _ = build_capture_frames(scan_count=1)
    frames_b, _ = build_capture_frames(scan_count=4)
    path_a = write_capture(tmp_path, "a.pcapng", frames_a)
    path_b = write_capture(tmp_path, "b.pcapng", frames_b)
    cap_a = load_capture(path_a, vid=THERMO_VID, pid=THERMO_PID)
    cap_b = load_capture(path_b, vid=THERMO_VID, pid=THERMO_PID)
    text = diff_captures(cap_a, cap_b)

    assert "differs" in text
    assert "4" in text
    assert "offset 4: A=0x01 B=0x04" in text
    # The identical STATUS_CMD must NOT be reported as exclusive to one side.
    assert "only in A (" not in text
    assert "only in B (" not in text


def test_extract_reassembles_interferogram(tmp_path):
    frames, interferogram = build_capture_frames(scan_count=1)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)

    out_path = tmp_path / "ep82.bin"
    written = extract(cap, endpoint=0x82, out_path=out_path, as_dtype="float32le")
    assert out_path in written
    assert out_path.read_bytes() == interferogram.tobytes()

    npy_path = out_path.with_suffix(out_path.suffix + ".npy")
    assert npy_path in written
    arr = np.load(npy_path)
    np.testing.assert_array_equal(arr, interferogram)


def test_extract_splits_bursts_on_gap(tmp_path):
    frames, _ = build_capture_frames(scan_count=1)
    # Add a second, separate interferogram burst far later in time on EP2.
    ids = itertools.count(10_000)
    t = 100.0
    second = synthetic_interferogram(512, center=256).tobytes()
    for off in range(0, len(second), 512):
        chunk = second[off : off + 512]
        irp = next(ids)
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 2, True, 3, True), t))
        frames.append((usbpcap_frame(irp, THERMO_BUS, THERMO_DEV, 2, True, 3, False, data=chunk), t + 0.0002))
        t += 0.0005
    frames.sort(key=lambda fd: fd[1])

    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)

    out_path = tmp_path / "burst.bin"
    written = extract(cap, endpoint=0x82, out_path=out_path, gap_ms=50)
    bin_files = [p for p in written if p.suffix == ".bin"]
    assert len(bin_files) == 2
    assert bin_files[0].name == "burst_000.bin"
    assert bin_files[1].name == "burst_001.bin"
    assert len(bin_files[1].read_bytes()) == len(second)


def test_guess_arrays_locates_centre_burst(tmp_path):
    frames, interferogram = build_capture_frames(scan_count=1, n_samples=2048, center=1024)
    path = write_capture(tmp_path, "cap.pcapng", frames)
    cap = load_capture(path, vid=THERMO_VID, pid=THERMO_PID)

    text = guess_arrays_report(cap, min_bytes=512)
    assert "float32le" in text
    assert "centre-burst candidate" in text

    # Also check the underlying structured result directly for the exact index.
    from tools.usbcap.analysis import guess_arrays_for_payload

    raw = b"".join(tr.data for tr in cap.transfers if tr.direction_in and tr.endpoint_address == 0x82)
    guesses = {g.dtype: g for g in guess_arrays_for_payload(raw)}
    assert "float32le" in guesses
    burst = guesses["float32le"].centre_burst
    assert burst is not None
    assert abs(burst.index - 1024) <= 3


def test_pairing_handles_recycled_irp_ids():
    # Windows reuses IRP addresses back to back; each submit/complete pair
    # must still become its own transfer (seen in a real Summit capture).
    from tools.usbcap.model import TransferType, UsbEvent, pair_events

    events = []
    for i in range(5):
        payload = f"<cmd>{i}</cmd>".encode()
        events.append(UsbEvent(t=i, bus=1, device=4, endpoint=1, direction_in=False,
                               transfer_type=TransferType.BULK, is_submit=True, status=0,
                               xfer_id=0xABC, data=payload))
        events.append(UsbEvent(t=i + 0.1, bus=1, device=4, endpoint=1, direction_in=False,
                               transfer_type=TransferType.BULK, is_submit=False, status=0,
                               xfer_id=0xABC))
    transfers = pair_events(events)
    assert [t.data for t in transfers] == [f"<cmd>{i}</cmd>".encode() for i in range(5)]
