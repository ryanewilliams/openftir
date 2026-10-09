"""Assembles EP 0x83 byte chunks into complete scans.

See ``docs/summit-protocol.md`` ("EP 0x83 data stream"): a byte stream (USB
transfer boundaries are meaningless) of 16-byte little-endian headers each
followed by a payload::

    offset 0   uint32  record type: 0x10000000 data, 0x90000000 end-of-scan
    offset 4   uint16  scan counter (global, increments per scan)
    offset 6   uint16  block index within the scan (0, 1, 2, ...)
    offset 8   uint32  0
    offset 12  uint32  payload length in bytes

Data payloads are float32 LE interferogram samples. The end-of-scan
record's payload isn't decoded (its length varies -- real captures have
shown 36-148 bytes, not a fixed size); it's kept verbatim as the third
element of each yielded tuple.
"""

from __future__ import annotations

import struct

import numpy as np

__all__ = ["ScanAssembler", "TYPE_DATA", "TYPE_END_OF_SCAN"]

TYPE_DATA = 0x10000000
TYPE_END_OF_SCAN = 0x90000000

_HEADER = struct.Struct("<IHHII")
_HEADER_SIZE = _HEADER.size  # 16

#: Sanity bound on a record's declared payload length, used both to reject
#: a corrupt header outright and to bound how far resync scans ahead. Real
#: payloads are tiny (a data block is ~40-41 floats = 160-164 bytes; a real
#: capture's end-of-scan payload has been seen anywhere from 44 to 148
#: bytes -- smaller than the doc's "128-byte" guess, and not fixed); this
#: is generous headroom.
_MAX_PAYLOAD = 1 << 20


def _looks_valid(magic: int, zero: int, length: int) -> bool:
    """Whether a 16-byte header looks like a real record, not corruption/
    desync. The offset-8 "zero" field reliably *is* zero for data records
    (seen in every capture analysed) but is NOT reliable for end-of-scan
    records (real captures show nonzero, varying values there -- docs call
    that record's payload "not yet decoded"), so it's only checked for
    ``TYPE_DATA``."""
    if not (0 <= length <= _MAX_PAYLOAD):
        return False
    if magic == TYPE_DATA:
        return zero == 0
    return magic == TYPE_END_OF_SCAN


class ScanAssembler:
    """Feed it arbitrary byte chunks from EP 0x83; it yields complete scans.

    Each item yielded by :meth:`feed` is ``(scan_id, samples, end_record)``:
    ``samples`` is a ``float32`` numpy array (all data blocks for that scan,
    concatenated in block order) and ``end_record`` is the raw (variable-
    length, undecoded) end-of-scan payload. A scan whose block indices show
    a gap (one or more missing blocks) or that was interrupted by a corrupt
    header is dropped silently -- it is never yielded.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._blocks: dict[int, list[tuple[int, bytes]]] = {}
        self._bad: set[int] = set()

    def feed(self, chunk: bytes) -> list[tuple[int, np.ndarray, bytes]]:
        self._buf.extend(chunk)
        results: list[tuple[int, np.ndarray, bytes]] = []

        while True:
            if len(self._buf) < _HEADER_SIZE:
                return results

            magic, scan_id, block, zero, length = _HEADER.unpack_from(self._buf, 0)
            if not _looks_valid(magic, zero, length):
                # Corrupt header: we can't tell which scan this would have
                # belonged to, so mark every scan currently in progress bad
                # (its continuation, if any, will be dropped at end-of-scan)
                # and scan forward for the next plausible header.
                self._bad.update(self._blocks)
                if not self._resync():
                    return results  # not enough data yet to find one
                continue

            if len(self._buf) < _HEADER_SIZE + length:
                return results  # wait for the rest of the payload

            payload = bytes(self._buf[_HEADER_SIZE : _HEADER_SIZE + length])
            del self._buf[: _HEADER_SIZE + length]

            if magic == TYPE_DATA:
                blocks = self._blocks.setdefault(scan_id, [])
                expected = blocks[-1][0] + 1 if blocks else 0
                if block != expected:
                    self._bad.add(scan_id)
                blocks.append((block, payload))
            else:  # end-of-scan
                blocks = self._blocks.pop(scan_id, [])
                was_bad = scan_id in self._bad
                self._bad.discard(scan_id)
                if was_bad or not blocks:
                    continue
                samples = np.frombuffer(b"".join(d for _, d in blocks), dtype="<f4").copy()
                results.append((scan_id, samples, payload))

    def _resync(self) -> bool:
        """Scan ``self._buf`` (skipping byte 0, which is known bad) for the
        next offset holding a plausible header. On success, discards
        everything before it and returns True; otherwise trims the buffer
        down to its last ``_HEADER_SIZE - 1`` bytes (so a header split across
        a future ``feed()`` call still gets a chance) and returns False."""
        buf = self._buf
        limit = len(buf) - _HEADER_SIZE
        for offset in range(1, limit + 1):
            magic, _scan_id, _block, zero, length = _HEADER.unpack_from(buf, offset)
            if _looks_valid(magic, zero, length):
                del buf[:offset]
                return True
        if len(buf) > _HEADER_SIZE - 1:
            del buf[: len(buf) - (_HEADER_SIZE - 1)]
        return False
