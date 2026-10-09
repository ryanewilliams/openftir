"""Validation against real captures of the user's own Summit X.

Everything here is skipped unless ``OPENFTIR_SUMMIT_DATA`` points at a
directory holding the real capture/decode files (see
``docs/summit-protocol.md``). That directory is private (the pcapng
captures embed the instrument's serial number) and is never committed to
this repo -- set the env var locally to run these.

Run with the data:   OPENFTIR_SUMMIT_DATA=/path/to/data pytest tests/test_summit_validation.py
Run without (skip):  pytest tests/test_summit_validation.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from openftir import io as ioapi
from openftir.instruments.base import CollectParams
from openftir.instruments.summit.driver import coadd_to_single_beam
from openftir.instruments.summit.stream import ScanAssembler
from openftir.processing import ratio
from openftir.spectrum import YUnit

_DATA_DIR = os.environ.get("OPENFTIR_SUMMIT_DATA")

pytestmark = pytest.mark.skipif(
    not _DATA_DIR,
    reason="OPENFTIR_SUMMIT_DATA not set -- real-capture validation data not available",
)

_LASER_FREQ = 11685.125977
_PEAK_POS = 6154  # 4 cm-1, docs/summit-protocol.md


def _data_dir() -> Path:
    assert _DATA_DIR is not None
    return Path(_DATA_DIR)


def _load_scans(prefix: str, ids: "list[int]") -> list[np.ndarray]:
    return [np.load(_data_dir() / f"{prefix}_scan{i}.npy") for i in ids]


def test_coadd_and_fft_matches_omnic_spa_within_tolerance():
    background_scans = _load_scans("bg10", [2, 3, 4, 5])
    sample_scans = _load_scans("s4", [20, 21, 22, 23])

    params = CollectParams(
        scans=4,
        resolution=4.0,
        x_min=600.0,
        x_max=4000.0,
        apodization="happ-genzel",
        zero_fill=2,
        phase_correction="mertz",
    )

    _bg_igram, background = coadd_to_single_beam(background_scans, _PEAK_POS, _LASER_FREQ, params)
    _s4_igram, sample = coadd_to_single_beam(sample_scans, _PEAK_POS, _LASER_FREQ, params)

    ours = ratio(sample, background, to=YUnit.TRANSMITTANCE)

    reference = ioapi.load(str(_data_dir() / "04.spa"))[0]
    assert reference.y_unit == YUnit.TRANSMITTANCE

    mask = (reference.x >= 650.0) & (reference.x <= 3950.0)
    ref_x = reference.x[mask]
    ref_y = reference.y[mask]

    order = np.argsort(ours.x)
    ours_interp = np.interp(ref_x, ours.x[order], ours.y[order])

    rms = float(np.sqrt(np.mean((ours_interp - ref_y) ** 2)))
    # The analysis behind docs/summit-protocol.md achieved 0.44 %T rms.
    assert rms < 0.6, f"rms difference vs 04.spa over 650-3950 cm-1: {rms:.3f} %T"


def test_scan_assembler_against_real_ep83_capture():
    pcap_path = _data_dir() / "6185ac80-04_sample_4scan_4cm.pcapng"
    if not pcap_path.exists():
        pytest.skip(f"{pcap_path.name} not present in OPENFTIR_SUMMIT_DATA")

    from tools.usbcap.model import events_from_frames, pair_events
    from tools.usbcap.pcap_reader import read_frames

    transfers = [t for t in pair_events(events_from_frames(read_frames(pcap_path))) if t.data]
    # Identify the Summit by the device that sends commands (EP 0x01, "<cmd>")
    # -- EP 0x83 (data) transfers on that same (bus, device) are the ones we
    # want, same filter the protocol-analysis tooling used.
    devices = {(t.bus, t.device) for t in transfers if t.endpoint == 1 and b"<cmd>" in t.data[:12]}
    assert devices, "no device sending <cmd> found in the capture"

    data_transfers = sorted(
        (t for t in transfers if (t.bus, t.device) in devices and t.endpoint == 3),
        key=lambda t: t.t_complete or t.t,
    )
    assert data_transfers, "no EP 0x83 data transfers found in the capture"

    assembler = ScanAssembler()
    results = []
    for transfer in data_transfers:
        results.extend(assembler.feed(transfer.data))

    scan_ids = [scan_id for scan_id, _samples, _end in results]
    assert set(scan_ids) >= {20, 21, 22, 23}

    for scan_id, samples, _end in results:
        if scan_id not in (20, 21, 22, 23):
            continue
        zpd = int(np.argmax(np.abs(samples - np.median(samples))))
        assert zpd in (6153, 6154), f"scan {scan_id}: ZPD at {zpd}, expected 6153-6154"
