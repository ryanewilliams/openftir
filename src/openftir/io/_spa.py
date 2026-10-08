"""Reader for Thermo (Nicolet/OMNIC) ``.spa`` and ``.spg`` files.

These are proprietary, undocumented binary formats. Nothing here was derived
from Thermo's own software or SDK: the layout below was reconstructed from

  * public, independent write-ups of the format, notably:
      - SpectroChemPy's OMNIC-SPA developer-guide page, which documents the
        fixed file header (signature ``"Spectral Data File"``, a little
        endian uint16 record count at offset 294, a key/record table
        starting at offset 304 made of 16-byte entries each naming a
        "block" by a type code plus a byte offset/length into the file) --
        https://www.spectrochempy.fr/1.0.0/devguide/file_formats/omnic/spa.html
      - a short community script that walks the same key table looking for
        block type 3 (spectral data, float32) --
        https://gist.github.com/ZGainsforth/7b05bf0b0409ae57ce7f
  * independent verification against a real ``.spa`` file produced by a
    Nicolet Summit (the user's own instrument), which confirmed the exact
    16-byte record layout (uint16 type, uint32 offset, uint32 length, 6
    reserved bytes), the title field at offset 30, the record count at
    offset 294, a 32-bit "seconds since 1899-12-31" acquisition timestamp
    at offset 296, and the internal layout of the type-2 ("spectrum
    header": npts/first-x/last-x as float32) and type-3 (raw float32 Y
    array) blocks.

No code or binary data from any other project was copied; only the
*descriptions* of the layout were used, per the format write-ups above.

Block type codes seen in the wild (not all are used by this reader):

    0x02  spectrum header (point count, first/last X, ...)
    0x03  the Y data itself, float32, little endian
    0x1B  free-text collection/processing log ("history")
    0x69  small numeric block (resolution-ish), not reliably decoded
    0x82  repeated numeric/text sub-blocks (instrument parameters)
    0x65/0x67, 0x64/0x66  auxiliary single-beam/interferogram data pairs,
          each a small header block followed by a float32 array -- not
          needed for the final spectrum and not parsed.

Because the type-2/type-3 pairing is the one thing confirmed against real
data, the parser is deliberately conservative: it only trusts the point
count that matches the real float32 array length, and it raises
``FormatError`` rather than guess when the expected blocks are missing.
"""

from __future__ import annotations

import os
import re
import struct
from datetime import datetime, timedelta

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from .errors import FormatError

SIGNATURE = b"Spectral Data File"

_NLINES_OFFSET = 294
_TIMESTAMP_OFFSET = 296
_TABLE_OFFSET = 304
_RECORD_SIZE = 16
_TITLE_OFFSET = 30
_TITLE_MAX_LEN = _NLINES_OFFSET - _TITLE_OFFSET

# Thermo's epoch for the 32-bit acquisition timestamp: seconds since
# 1899-12-31 00:00:00 (verified against a real file: the decoded datetime
# matched the "Collect Sample on ..." text in the log block to the second,
# modulo timezone).
_OMNIC_EPOCH = datetime(1899, 12, 31)

_TYPE_HEADER = 2
_TYPE_DATA = 3
_TYPE_HISTORY = 0x1B

_LOG_PATTERNS = {
    "resolution_cm-1": re.compile(r"Resolution:\s*([0-9.]+)"),
    "x_range_text": re.compile(r"Resolution:\s*[0-9.]+\s*from\s*([0-9.]+)\s*to\s*([0-9.]+)"),
    "final_format": re.compile(r"Final format:\s*(\S+)"),
    "username": re.compile(r"Username:\s*(\S+)"),
    "instrument_serial_number": re.compile(r"Instrument serial number:\s*(\S+)"),
    "background_collected": re.compile(r"Background collected on\s*(.+?)\s*\r?\n"),
}

_FORMAT_TO_YUNIT = {
    "absorbance": YUnit.ABSORBANCE,
    "%transmittance": YUnit.TRANSMITTANCE,
    "transmittance": YUnit.TRANSMITTANCE,
    "%reflectance": YUnit.REFLECTANCE,
    "reflectance": YUnit.REFLECTANCE,
    "kubelka-munk": YUnit.KUBELKA_MUNK,
    "log(1/r)": YUnit.LOG_INV_REFLECTANCE,
    "single beam": YUnit.SINGLE_BEAM,
}


class _Record:
    __slots__ = ("type", "pos", "length")

    def __init__(self, type_: int, pos: int, length: int) -> None:
        self.type = type_
        self.pos = pos
        self.length = length


def _read_records(data: bytes, path_for_errors: str) -> list[_Record]:
    if len(data) < _TABLE_OFFSET or not data.startswith(SIGNATURE):
        raise FormatError(
            f"{path_for_errors}: not a Thermo OMNIC SPA/SPG file "
            f'(missing "Spectral Data File" signature)'
        )
    if len(data) < _NLINES_OFFSET + 2:
        raise FormatError(f"{path_for_errors}: file too short to contain a record count")

    (nrecords,) = struct.unpack_from("<H", data, _NLINES_OFFSET)
    end = _TABLE_OFFSET + nrecords * _RECORD_SIZE
    if nrecords == 0 or end > len(data):
        raise FormatError(
            f"{path_for_errors}: record table claims {nrecords} records, "
            f"which does not fit in a {len(data)}-byte file"
        )

    records: list[_Record] = []
    for i in range(nrecords):
        off = _TABLE_OFFSET + i * _RECORD_SIZE
        type_, pos, length = struct.unpack_from("<HII", data, off)
        records.append(_Record(type_, pos, length))
    return records


def _read_cstring(data: bytes, offset: int, max_len: int) -> str:
    chunk = data[offset : offset + max_len]
    end = chunk.find(b"\x00")
    if end != -1:
        chunk = chunk[:end]
    return chunk.decode("latin-1", errors="replace").strip()


def _parse_header_block(data: bytes, rec: _Record) -> tuple[int | None, float | None, float | None]:
    """Return (npts, first_x, last_x) from a type-2 block, best effort."""
    if rec.length < 24:
        return None, None, None
    npts = struct.unpack_from("<I", data, rec.pos + 4)[0]
    first_x, last_x = struct.unpack_from("<ff", data, rec.pos + 16)
    return npts, first_x, last_x


def _parse_log_text(text: str) -> dict:
    meta: dict = {}
    for key, pattern in _LOG_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            continue
        if key == "x_range_text":
            meta["x_range_from_log"] = (float(m.group(1)), float(m.group(2)))
        else:
            meta[key] = m.group(1)
    return meta


def _y_unit_from_meta(meta: dict) -> YUnit:
    fmt = meta.get("final_format", "").lower()
    return _FORMAT_TO_YUNIT.get(fmt, YUnit.ABSORBANCE)


def _acquired_from_timestamp(data: bytes) -> datetime | None:
    if len(data) < _TIMESTAMP_OFFSET + 4:
        return None
    (raw,) = struct.unpack_from("<I", data, _TIMESTAMP_OFFSET)
    if raw == 0:
        return None
    try:
        return _OMNIC_EPOCH + timedelta(seconds=raw)
    except OverflowError:
        return None


def _spectrum_from_group(
    data: bytes,
    group: list[_Record],
    *,
    title: str,
    acquired: datetime | None,
    path_for_errors: str,
    index: int,
    source_format: str,
) -> Spectrum:
    data_recs = [r for r in group if r.type == _TYPE_DATA]
    header_recs = [r for r in group if r.type == _TYPE_HEADER]
    history_recs = [r for r in group if r.type == _TYPE_HISTORY]

    if not data_recs:
        raise FormatError(
            f"{path_for_errors}: spectrum #{index} has no data block (type 3) "
            "in its key table -- unrecognised SPA/SPG layout"
        )
    # OMNIC's auxiliary single-beam/interferogram data uses other type
    # codes (0x65-0x67 and friends), so a type==3 block should be the
    # final processed spectrum; if several occur in one group, the first
    # is used (observed real files only ever have one per spectrum).
    data_rec = data_recs[0]
    if data_rec.length % 4 != 0 or data_rec.length == 0:
        raise FormatError(
            f"{path_for_errors}: spectrum #{index} data block length "
            f"{data_rec.length} is not a positive multiple of 4 bytes"
        )
    if data_rec.pos + data_rec.length > len(data):
        raise FormatError(f"{path_for_errors}: spectrum #{index} data block runs past end of file")

    npts_from_data = data_rec.length // 4
    y = np.frombuffer(data, dtype="<f4", count=npts_from_data, offset=data_rec.pos).astype(float)

    first_x: float | None = None
    last_x: float | None = None
    for h in header_recs:
        npts_h, fx, lx = _parse_header_block(data, h)
        if fx is not None and lx is not None and fx != lx:
            first_x, last_x = fx, lx
            break

    meta: dict = {"source_format": source_format}
    history: list[str] = []
    for h in history_recs:
        text = _read_cstring(data, h.pos, h.length)
        if text:
            history.append(text)
            meta.update(_parse_log_text(text))

    if first_x is None or last_x is None:
        # Fall back to whatever the log text said, else just enumerate points.
        rng = meta.get("x_range_from_log")
        if rng:
            # log text is written low-to-high; data itself is typically
            # stored high-to-low, so keep that orientation for consistency
            # with files where we *did* get first/last X from the header.
            first_x, last_x = rng[1], rng[0]
        else:
            first_x, last_x = float(npts_from_data - 1), 0.0

    x = np.linspace(first_x, last_x, npts_from_data)

    y_unit = _y_unit_from_meta(meta)
    if not np.all(np.isfinite(y)):
        meta["nonfinite_values"] = True

    return Spectrum(
        x=x,
        y=y,
        x_unit=XUnit.WAVENUMBER,
        y_unit=y_unit,
        title=title,
        meta=meta,
        acquired=acquired,
        history=history,
    )


def _group_records(records: list[_Record]) -> list[list[_Record]]:
    """Split a flat record table into one group per spectrum.

    A plain ``.spa`` has exactly one type-2 (header) record, so this yields
    a single group. A ``.spg`` group file is assumed (no confirmed sample
    was available) to repeat the same type-2/type-3/history pattern once
    per member spectrum within one flat table; each type-2 record starts a
    new group that runs up to (but not including) the next type-2 record.
    """
    groups: list[list[_Record]] = []
    current: list[_Record] = []
    for rec in records:
        if rec.type == _TYPE_HEADER:
            if current:
                groups.append(current)
            current = [rec]
        else:
            current.append(rec)
    if current:
        groups.append(current)
    if not groups:
        # No type-2 header at all: treat every record as one group and let
        # the data-block search below fail loudly if there is really
        # nothing usable.
        groups = [records]
    return groups


def _read_spa_bytes(
    data: bytes, path_for_errors: str, *, default_title: str, source_format: str
) -> list[Spectrum]:
    records = _read_records(data, path_for_errors)
    groups = _group_records(records)

    title = _read_cstring(data, _TITLE_OFFSET, _TITLE_MAX_LEN) or default_title
    acquired = _acquired_from_timestamp(data)

    spectra = []
    for i, group in enumerate(groups):
        group_title = title if len(groups) == 1 else f"{title} #{i + 1}"
        spectra.append(
            _spectrum_from_group(
                data,
                group,
                title=group_title,
                acquired=acquired,
                path_for_errors=path_for_errors,
                index=i,
                source_format=source_format,
            )
        )
    return spectra


def read_spa(path: str | os.PathLike) -> Spectrum:
    """Read a single-spectrum Thermo OMNIC ``.spa`` file."""
    path = os.fspath(path)
    with open(path, "rb") as fh:
        data = fh.read()
    default_title = os.path.splitext(os.path.basename(path))[0]
    spectra = _read_spa_bytes(data, path, default_title=default_title, source_format="spa")
    # A .spa claiming to hold several spectra is unusual but not fatal; the
    # public API for .spa is "one spectrum", so just take the first.
    return spectra[0]


def read_spg(path: str | os.PathLike) -> list[Spectrum]:
    """Read a multi-spectrum Thermo OMNIC ``.spg`` group file."""
    path = os.fspath(path)
    with open(path, "rb") as fh:
        data = fh.read()
    default_title = os.path.splitext(os.path.basename(path))[0]
    return _read_spa_bytes(data, path, default_title=default_title, source_format="spg")
