"""Tests for ``openftir.instruments.summit.stream.ScanAssembler``."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from openftir.instruments.summit.stream import TYPE_DATA, TYPE_END_OF_SCAN, ScanAssembler

_HEADER = struct.Struct("<IHHII")


def _data_record(scan_id: int, block: int, floats: list[float]) -> bytes:
    payload = np.asarray(floats, dtype="<f4").tobytes()
    return _HEADER.pack(TYPE_DATA, scan_id, block, 0, len(payload)) + payload


def _end_record(scan_id: int, block: int, payload_len: int = 128) -> bytes:
    payload = bytes(payload_len)
    return _HEADER.pack(TYPE_END_OF_SCAN, scan_id, block, 0, len(payload)) + payload


def _one_scan(scan_id: int, n_floats: int = 80, block_size: int = 40) -> bytes:
    values = [float(i) for i in range(n_floats)]
    out = bytearray()
    block = 0
    for offset in range(0, n_floats, block_size):
        out += _data_record(scan_id, block, values[offset : offset + block_size])
        block += 1
    out += _end_record(scan_id, block)
    return bytes(out)


def test_single_scan_whole_chunk():
    stream = _one_scan(1, n_floats=80, block_size=40)
    assembler = ScanAssembler()
    results = assembler.feed(stream)
    assert len(results) == 1
    scan_id, samples, end_record = results[0]
    assert scan_id == 1
    assert samples.dtype == np.float32
    np.testing.assert_allclose(samples, np.arange(80, dtype=np.float32))
    assert end_record == bytes(128)


def test_chunk_boundaries_anywhere():
    stream = _one_scan(1, n_floats=123, block_size=41) + _one_scan(2, n_floats=82, block_size=40)
    assembler = ScanAssembler()
    all_results = []
    # Feed one byte at a time -- the hardest possible chunking.
    for i in range(len(stream)):
        all_results += assembler.feed(stream[i : i + 1])
    assert [r[0] for r in all_results] == [1, 2]
    np.testing.assert_allclose(all_results[0][1], np.arange(123, dtype=np.float32))
    np.testing.assert_allclose(all_results[1][1], np.arange(82, dtype=np.float32))


def test_chunk_boundaries_mid_header_and_mid_payload():
    stream = _one_scan(1, n_floats=90, block_size=40)
    assembler = ScanAssembler()
    # Split right in the middle of the first record's header, and again in
    # the middle of a payload.
    cuts = [0, 7, 16, 100, len(stream)]
    results = []
    for a, b in zip(cuts, cuts[1:]):
        results += assembler.feed(stream[a:b])
    assert len(results) == 1
    np.testing.assert_allclose(results[0][1], np.arange(90, dtype=np.float32))


def test_multiple_scans_in_one_feed_call():
    stream = b"".join(_one_scan(sid, n_floats=41, block_size=41) for sid in (5, 6, 7))
    assembler = ScanAssembler()
    results = assembler.feed(stream)
    assert [r[0] for r in results] == [5, 6, 7]


def test_partial_final_scan_is_ignored():
    complete = _one_scan(1, n_floats=80, block_size=40)
    partial = _data_record(2, 0, [1.0] * 40)  # no end-of-scan record
    assembler = ScanAssembler()
    results = assembler.feed(complete + partial)
    assert len(results) == 1
    assert results[0][0] == 1
    # the partial scan is buffered, waiting for more data -- never yielded
    assert assembler._blocks.keys() == {2}


def test_corruption_drops_only_the_affected_scan():
    good1 = _one_scan(1, n_floats=80, block_size=40)
    good2 = _one_scan(2, n_floats=80, block_size=40)
    corrupted_middle = bytearray(good2)
    # Flip every byte of the *second* record's 16-byte header (offset 176 =
    # first record's header(16) + payload(160)) -- that's guaranteed to
    # desync the framing, unlike corrupting payload bytes (which would just
    # change sample values without breaking anything).
    second_header_offset = 16 + 40 * 4
    for i in range(second_header_offset, second_header_offset + 16):
        corrupted_middle[i] ^= 0xFF
    good3 = _one_scan(3, n_floats=80, block_size=40)

    stream = good1 + bytes(corrupted_middle) + good3
    assembler = ScanAssembler()
    results = assembler.feed(stream)
    ids = [r[0] for r in results]
    assert 1 in ids
    assert 3 in ids
    assert 2 not in ids  # dropped: its block framing was corrupted


def test_block_index_gap_drops_scan():
    # Blocks 0 and 2, missing 1 -- a gap without any header corruption.
    out = bytearray()
    out += _data_record(1, 0, [1.0] * 10)
    out += _data_record(1, 2, [2.0] * 10)  # gap: expected block 1
    out += _end_record(1, 3)
    out += _one_scan(2, n_floats=40, block_size=40)

    assembler = ScanAssembler()
    results = assembler.feed(out)
    assert [r[0] for r in results] == [2]


def test_feed_returns_empty_list_when_nothing_complete():
    assembler = ScanAssembler()
    assert assembler.feed(b"") == []
    assert assembler.feed(bytes(8)) == []  # shorter than one header


def test_resync_after_garbage_prefix():
    garbage = b"\x00" * 5 + b"\xff" * 11  # 16 bytes of nonsense, no valid header
    stream = garbage + _one_scan(1, n_floats=40, block_size=40)
    assembler = ScanAssembler()
    results = assembler.feed(stream)
    assert len(results) == 1
    assert results[0][0] == 1
