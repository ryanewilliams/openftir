"""Tests for the Thermo OMNIC .spa/.spg reader.

Covers two things:

1. A synthetic ``.spa``/``.spg`` byte stream built from scratch following
   the documented layout (signature, record-count, 16-byte record table,
   type-2 header block, type-3 float32 data block, type-0x1B history
   text). This is the deterministic, always-available test.
2. If the environment variable ``OPENFTIR_REAL_SPA`` points at a real
   ``.spa`` file (e.g. the user's own instrument export), parse it too and
   sanity-check the result. Skipped otherwise -- real instrument files are
   private data and are never committed to the repo.
"""

from __future__ import annotations

import os
import struct

import numpy as np
import pytest

from openftir.io import FormatError, load
from openftir.io._spa import read_spa, read_spg
from openftir.spectrum import XUnit, YUnit

SIGNATURE = b"Spectral Data File"
RECORD_SIZE = 16
TABLE_OFFSET = 304
NLINES_OFFSET = 294
TIMESTAMP_OFFSET = 296
TITLE_OFFSET = 30


def _pad(data: bytearray, length: int) -> None:
    if len(data) < length:
        data.extend(b"\x00" * (length - len(data)))


def build_spa_bytes(
    *,
    title: str = "synthetic",
    npts: int = 50,
    first_x: float = 4000.0,
    last_x: float = 400.0,
    y: np.ndarray | None = None,
    history_text: str | None = "Collect Sample on Wednesday, January 1, 2025 12:00:00 PM (GMT+00:00)\r\n\tFinal format: Absorbance\r\n\tResolution: 4.000 from 400.000 to 4000.000\r\n\tUsername: TESTER\r\n\tInstrument serial number: SN0001\r\n\x00",
    timestamp: int = 3999970126,
    extra_header_blocks: int = 0,
) -> bytes:
    """Build a minimal but structurally valid .spa byte stream."""
    if y is None:
        rng = np.random.default_rng(0)
        y = rng.normal(0.1, 0.02, size=npts).astype(np.float32)
    else:
        y = np.asarray(y, dtype=np.float32)
        npts = len(y)

    data = bytearray()
    data.extend(SIGNATURE)
    _pad(data, TITLE_OFFSET)
    title_bytes = title.encode("latin-1") + b"\x00"
    data[TITLE_OFFSET : TITLE_OFFSET + len(title_bytes)] = title_bytes
    # Pad all the way to the record table now so later pack_into() calls at
    # NLINES_OFFSET/TIMESTAMP_OFFSET (both < TABLE_OFFSET) have room.
    _pad(data, TABLE_OFFSET)

    # Blocks are appended after the fixed header/table region; remember
    # their file offsets as we go.
    body_start = TABLE_OFFSET + (3 + extra_header_blocks) * RECORD_SIZE
    body = bytearray()

    def append_block(payload: bytes) -> tuple[int, int]:
        pos = body_start + len(body)
        body.extend(payload)
        return pos, len(payload)

    # type-2 header block: int32 x3 'junk', npts, then firstX/lastX float32
    header_payload = struct.pack("<iiii", 0, npts, 1, 16) + struct.pack("<ff", first_x, last_x)
    header_payload += b"\x00" * 40  # pad out like real files do
    header_pos, header_len = append_block(header_payload)

    data_payload = y.tobytes()
    data_pos, data_len = append_block(data_payload)

    hist_pos = hist_len = 0
    if history_text is not None:
        hist_payload = history_text.encode("latin-1")
        hist_pos, hist_len = append_block(hist_payload)

    records = [(2, header_pos, header_len), (3, data_pos, data_len)]
    if history_text is not None:
        records.append((0x1B, hist_pos, hist_len))
    for _ in range(extra_header_blocks):
        records.append((1, 0, 0))

    nlines = len(records)
    struct.pack_into("<H", data, NLINES_OFFSET, nlines)
    struct.pack_into("<I", data, TIMESTAMP_OFFSET, timestamp)

    _pad(data, TABLE_OFFSET)
    for typ, pos, length in records:
        rec = struct.pack("<HII", typ, pos, length) + b"\x00" * 6
        data.extend(rec)
    _pad(data, body_start)
    data.extend(body)
    return bytes(data)


def test_read_spa_synthetic(tmp_path):
    y = np.linspace(0.0, 1.0, 50).astype(np.float32)
    raw = build_spa_bytes(title="my sample", npts=50, first_x=4000.0, last_x=400.0, y=y)
    path = tmp_path / "sample.spa"
    path.write_bytes(raw)

    spectrum = read_spa(path)
    assert spectrum.title == "my sample"
    assert len(spectrum) == 50
    assert spectrum.x[0] == pytest.approx(4000.0)
    assert spectrum.x[-1] == pytest.approx(400.0)
    np.testing.assert_allclose(spectrum.y, y, atol=1e-6)
    assert spectrum.x_unit == XUnit.WAVENUMBER
    assert spectrum.y_unit == YUnit.ABSORBANCE
    assert spectrum.acquired is not None
    assert spectrum.meta["resolution_cm-1"] == "4.000"
    assert spectrum.meta["username"] == "TESTER"
    assert spectrum.history


def test_read_spa_transmittance_format(tmp_path):
    raw = build_spa_bytes(
        history_text="Collect Sample on Monday\r\n\tFinal format: %Transmittance\r\n\tResolution: 8.000 from 650.000 to 4000.000\r\n\x00",
    )
    path = tmp_path / "t.spa"
    path.write_bytes(raw)
    spectrum = read_spa(path)
    assert spectrum.y_unit == YUnit.TRANSMITTANCE


def test_read_spa_via_dispatcher(tmp_path):
    raw = build_spa_bytes()
    path = tmp_path / "sample.spa"
    path.write_bytes(raw)
    spectra = load(path)
    assert len(spectra) == 1


def test_read_spa_bad_signature(tmp_path):
    path = tmp_path / "bad.spa"
    path.write_bytes(b"not a spa file" + b"\x00" * 400)
    with pytest.raises(FormatError):
        read_spa(path)


def test_read_spa_truncated_table(tmp_path):
    raw = bytearray(build_spa_bytes())
    # Claim far more records than the file can hold.
    struct.pack_into("<H", raw, NLINES_OFFSET, 5000)
    path = tmp_path / "trunc.spa"
    path.write_bytes(bytes(raw))
    with pytest.raises(FormatError):
        read_spa(path)


def test_read_spa_no_data_block(tmp_path):
    raw = build_spa_bytes()
    # Rewrite the table so that the only record left is a bogus type.
    data = bytearray(raw)
    struct.pack_into("<H", data, NLINES_OFFSET, 1)
    struct.pack_into("<HII", data, TABLE_OFFSET, 99, 0, 0)
    path = tmp_path / "nodata.spa"
    path.write_bytes(bytes(data))
    with pytest.raises(FormatError):
        read_spa(path)


def test_read_spg_multiple_spectra(tmp_path):
    """Build a crude multi-member .spg: two concatenated type2/type3/history
    groups sharing a single top-level record table, matching the repeating
    layout our reader's generic group-splitter expects."""
    y1 = np.linspace(0.0, 1.0, 30).astype(np.float32)
    y2 = np.linspace(1.0, 0.0, 30).astype(np.float32)

    data = bytearray()
    data.extend(SIGNATURE)
    _pad(data, TITLE_OFFSET)
    data[TITLE_OFFSET : TITLE_OFFSET + 5] = b"group"
    _pad(data, TABLE_OFFSET)

    records = []
    body_start = TABLE_OFFSET + 6 * RECORD_SIZE
    body = bytearray()

    def append(payload: bytes) -> tuple[int, int]:
        pos = body_start + len(body)
        body.extend(payload)
        return pos, len(payload)

    for y, first_x, last_x in [(y1, 4000.0, 400.0), (y2, 4000.0, 400.0)]:
        header_payload = struct.pack("<iiii", 0, len(y), 1, 16) + struct.pack("<ff", first_x, last_x)
        header_payload += b"\x00" * 40
        pos, length = append(header_payload)
        records.append((2, pos, length))
        pos, length = append(y.tobytes())
        records.append((3, pos, length))

    struct.pack_into("<H", data, NLINES_OFFSET, len(records))
    struct.pack_into("<I", data, TIMESTAMP_OFFSET, 3999970126)
    _pad(data, TABLE_OFFSET)
    for typ, pos, length in records:
        data.extend(struct.pack("<HII", typ, pos, length) + b"\x00" * 6)
    _pad(data, body_start)
    data.extend(body)

    path = tmp_path / "group.spg"
    path.write_bytes(bytes(data))

    spectra = read_spg(path)
    assert len(spectra) == 2
    np.testing.assert_allclose(spectra[0].y, y1, atol=1e-6)
    np.testing.assert_allclose(spectra[1].y, y2, atol=1e-6)
    assert spectra[0].title != spectra[1].title


@pytest.mark.skipif(
    not os.environ.get("OPENFTIR_REAL_SPA"),
    reason="set OPENFTIR_REAL_SPA=/path/to/file.spa to test against a real instrument export",
)
def test_read_real_spa_from_env():
    path = os.environ["OPENFTIR_REAL_SPA"]
    spectrum = read_spa(path)
    assert len(spectrum) > 10
    assert np.all(np.isfinite(spectrum.y))
    lo, hi = sorted((float(spectrum.x[0]), float(spectrum.x[-1])))
    assert 0 <= lo < 2000
    assert 2000 < hi <= 8000
    assert spectrum.title
