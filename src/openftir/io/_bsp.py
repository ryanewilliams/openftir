"""Varian Resolutions Pro spectrum files (``.bsp``).

A ``.bsp`` is an OLE2 compound document (the container old Office files
use). Each spectrum lives in its own stream under ``Spectra/``, listed in
``Spectra/IndexTable``. A spectrum stream holds, one after another:

* a ``Data`` block: the spectrum itself as little-endian doubles, on an
  evenly spaced axis ``x = (first + i) * dx``;
* a ``Parms`` block with the axis labels (e.g. "Wavenumber",
  "%Transmittance", "Response" for a single beam);
* ``Properties``: name/value text pairs for the collection settings
  (Resolution, Scans, Time Stamp, Effective Laser Wavenumber, ...);
* for collected spectra, the raw ``Interferogram`` as a second ``Data``
  block (volts against optical retardation), and a processing ``History``.

The layout was worked out from the lab's own files (see
docs/varian-protocol.md); only the parts OpenFTIR needs are read.
"""

from __future__ import annotations

import os
import re
import struct
from datetime import datetime

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from .errors import FormatError

_DATA_TAG = b"\x04\x00\x00\x00Data"
# Offsets inside a Data block, counted from its tag's length field.
_DX = 36
_FIRST = 48
_COUNT = 56
_NBYTES = 60
_VALUES = 64

# Every Properties entry's value is preceded by these fields.
_PROP_PREFIX = struct.pack("<7I", 4, 1, 4, 2, 4, 4, 4)

_Y_UNITS = {
    "%transmittance": YUnit.TRANSMITTANCE,
    "transmittance": YUnit.TRANSMITTANCE,
    "%reflectance": YUnit.REFLECTANCE,
    "reflectance": YUnit.REFLECTANCE,
    "absorbance": YUnit.ABSORBANCE,
    "kubelka-munk": YUnit.KUBELKA_MUNK,
    "log(1/r)": YUnit.LOG_INV_REFLECTANCE,
    "response": YUnit.SINGLE_BEAM,
    "single beam": YUnit.SINGLE_BEAM,
    "volts": YUnit.INTERFEROGRAM,
}

_TIME_FORMAT = "%A, %B %d, %Y %H:%M:%S"


def _read_data_block(stream: bytes, tag_at: int, where: str) -> tuple[np.ndarray, int, float]:
    """``(values, first, dx)`` of the Data block whose tag starts at ``tag_at``."""
    try:
        dx = struct.unpack_from("<d", stream, tag_at + _DX)[0]
        first = struct.unpack_from("<i", stream, tag_at + _FIRST)[0]
        count = struct.unpack_from("<I", stream, tag_at + _COUNT)[0]
        nbytes = struct.unpack_from("<I", stream, tag_at + _NBYTES)[0]
    except struct.error as exc:
        raise FormatError(f"{where}: truncated data block") from exc
    if nbytes != count * 8 or tag_at + _VALUES + nbytes > len(stream):
        raise FormatError(f"{where}: data block size doesn't match its point count ({count})")
    values = np.frombuffer(stream, "<f8", count, tag_at + _VALUES).copy()
    return values, first, dx


def _axis_labels(stream: bytes, after: int) -> tuple[str, str]:
    """The x and y labels from the Parms block following ``after``."""
    i = stream.find(b"Parms", after)
    if i < 0:
        return "", ""
    labels = re.findall(rb"[%A-Za-z][%A-Za-z0-9 ()/\-]{2,}", stream[i + 5:i + 200])
    names = [s.decode("latin-1") for s in labels if s not in (b"Properties",)]
    return (names[0] if names else ""), (names[1] if len(names) > 1 else "")


def parse_properties(stream: bytes) -> dict[str, str]:
    """Name/value text pairs (first occurrence of each name wins)."""
    props: dict[str, str] = {}
    for match in re.finditer(re.escape(_PROP_PREFIX), stream):
        end = match.start()
        name = None
        for length in range(1, 64):
            start = end - length
            if start >= 8 and struct.unpack_from("<II", stream, start - 8) == (length, length):
                name = stream[start:end]
                break
        if name is None:
            continue
        p = match.end()
        if p + 16 > len(stream):
            continue
        n1, four, n2, n3 = struct.unpack_from("<4I", stream, p)
        if four != 4 or not (n1 == n2 == n3) or n1 > 4096:
            continue
        key = name.decode("latin-1")
        props.setdefault(key, stream[p + 16:p + 16 + n1].decode("latin-1"))
    return props


def _float_or_none(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def parse_spectrum_stream(stream: bytes, where: str = "bsp", interferogram: bool = False) -> Spectrum:
    """One ``Spectra/<id>`` stream as a Spectrum (or its raw interferogram)."""
    first_tag = stream.find(_DATA_TAG)
    if first_tag < 0:
        raise FormatError(f"{where}: no spectrum data found")
    props = parse_properties(stream)

    if interferogram:
        marker = stream.find(b"Interferogram")
        tag = stream.find(_DATA_TAG, marker) if marker >= 0 else -1
        if tag < 0:
            raise FormatError(f"{where}: no interferogram stored")
        values, first, dx = _read_data_block(stream, tag, where)
        x = (first + np.arange(values.size)).astype(float)
        x_unit, y_unit = XUnit.POINTS, YUnit.INTERFEROGRAM
        meta_extra = {"retardation_step_cm": dx, "zpd_index": -first}
    else:
        values, first, dx = _read_data_block(stream, first_tag, where)
        x_label, y_label = _axis_labels(stream, first_tag)
        x = (first + np.arange(values.size)) * dx
        x_unit = XUnit.WAVENUMBER if x_label.lower().startswith("wavenumber") else XUnit.POINTS
        y_unit = _Y_UNITS.get(y_label.lower(), YUnit.ARBITRARY)
        meta_extra = {"x_label": x_label, "y_label": y_label, "point_spacing": dx}

    acquired = None
    stamp = props.get("Time Stamp", "")
    try:
        acquired = datetime.strptime(stamp.strip(), _TIME_FORMAT) if stamp else None
    except ValueError:
        acquired = None

    scans = props.get("Scans")
    if y_unit == YUnit.SINGLE_BEAM or scans in (None, "", "0"):
        scans = props.get("Background Scans", scans)
    meta = {
        "source_format": "bsp",
        "instrument": "Varian (Resolutions Pro)",
        "resolution": _float_or_none(props.get("Resolution")),
        "scans": int(_float_or_none(scans) or 0) or None,
        "laser_wavenumber": _float_or_none(props.get("Effective Laser Wavenumber")),
        "bsp_properties": props,
        **meta_extra,
    }
    comment = (props.get("Sample Comment") or "").strip()
    title = comment or ("Background" if y_unit == YUnit.SINGLE_BEAM else "Sample")
    return Spectrum(x=x, y=values, x_unit=x_unit, y_unit=y_unit, title=title, meta=meta, acquired=acquired)


def _stream_order(ole, where: str) -> list[str]:
    """Spectrum stream names in the order the file lists them."""
    names = [e[1] for e in ole.listdir() if len(e) == 2 and e[0] == "Spectra" and e[1] != "IndexTable"]
    if ole.exists("Spectra/IndexTable"):
        table = ole.openstream("Spectra/IndexTable").read()
        listed = [m.decode("latin-1") for m in re.findall(rb"[0-9A-F]{8}-[0-9A-F]{8}", table)]
        names = [n for n in listed if n in names] + [n for n in names if n not in listed]
    # Oldest first (the names start with a counter), so the background
    # comes before the sample measured against it.
    return sorted(names)


def read_bsp(path: str | os.PathLike, interferograms: bool = False) -> list[Spectrum]:
    """Every spectrum in a Resolutions Pro ``.bsp`` file.

    With ``interferograms=True`` the raw interferograms stored alongside
    collected spectra are returned as well, right after their spectrum.
    """
    import olefile

    path = os.fspath(path)
    if not olefile.isOleFile(path):
        raise FormatError(f"{path}: not a Resolutions Pro .bsp file (not an OLE2 document)")
    with olefile.OleFileIO(path) as ole:
        if not ole.exists("Spectra"):
            raise FormatError(f"{path}: no Spectra in this .bsp file")
        spectra: list[Spectrum] = []
        stem = os.path.splitext(os.path.basename(path))[0]
        for name in _stream_order(ole, path):
            stream = ole.openstream(f"Spectra/{name}").read()
            where = f"{path} [{name}]"
            spectrum = parse_spectrum_stream(stream, where)
            spectrum.title = f"{stem} - {spectrum.title.lower()}" if spectrum.title in ("Background", "Sample") else spectrum.title
            spectra.append(spectrum)
            if interferograms and b"Interferogram" in stream:
                ifg = parse_spectrum_stream(stream, where, interferogram=True)
                ifg.title = f"{spectrum.title} (interferogram)"
                spectra.append(ifg)
    if not spectra:
        raise FormatError(f"{path}: .bsp file contains no spectra")
    return spectra
