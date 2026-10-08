"""Plain delimited-text reader/writer (CSV/TSV/whitespace, two or more columns)."""

from __future__ import annotations

import csv
import io
import os
import re

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from .errors import FormatError

_DELIMITERS = [",", ";", "\t"]

_HEADER_UNIT_RE = re.compile(r"^#\s*([a-zA-Z_][\w ]*?)\s*:\s*(.+)$")

_XUNIT_BY_NAME = {u.value: u for u in XUnit} | {u.name.lower(): u for u in XUnit}
_YUNIT_BY_NAME = {u.value: u for u in YUnit} | {u.name.lower(): u for u in YUnit}


def _sniff_delimiter(sample_lines: list[str]) -> str | None:
    counts = {d: 0 for d in _DELIMITERS}
    for line in sample_lines:
        for d in _DELIMITERS:
            counts[d] += line.count(d)
    best = max(counts, key=lambda d: counts[d])
    if counts[best] > 0:
        return best
    return None  # fall back to whitespace splitting


def _split_fields(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        return line.split()
    reader = csv.reader(io.StringIO(line), delimiter=delimiter)
    return next(reader, [])


def _to_float(token: str) -> float:
    token = token.strip()
    if not token:
        raise ValueError("empty numeric field")
    try:
        return float(token)
    except ValueError:
        # decimal-comma tolerance: "1,23" -> "1.23". Only safe to try when
        # the token has exactly one comma and no dot already.
        if token.count(",") == 1 and "." not in token:
            return float(token.replace(",", "."))
        raise


def _looks_numeric(fields: list[str]) -> bool:
    if not fields:
        return False
    try:
        for f in fields:
            _to_float(f)
    except ValueError:
        return False
    return True


def read_csv(path: str | os.PathLike) -> list[Spectrum]:
    path = os.fspath(path)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        raw_lines = fh.readlines()

    meta_overrides: dict = {}
    data_lines: list[str] = []
    for line in raw_lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if not stripped.strip():
            continue
        if stripped.lstrip().startswith("#"):
            m = _HEADER_UNIT_RE.match(stripped.strip())
            if m:
                meta_overrides[m.group(1).strip().lower()] = m.group(2).strip()
            continue
        data_lines.append(stripped)

    if not data_lines:
        raise FormatError(f"{path}: no data rows found")

    delimiter = _sniff_delimiter(data_lines[:10])

    header_fields: list[str] | None = None
    first_fields = _split_fields(data_lines[0], delimiter)
    if not _looks_numeric(first_fields):
        header_fields = first_fields
        data_lines = data_lines[1:]
        if not data_lines:
            raise FormatError(f"{path}: file has only a header row, no data")

    rows: list[list[float]] = []
    ncols = None
    for line in data_lines:
        fields = _split_fields(line, delimiter)
        if not fields:
            continue
        try:
            values = [_to_float(f) for f in fields]
        except ValueError as exc:
            raise FormatError(f"{path}: could not parse data row {line!r} as numbers") from exc
        if ncols is None:
            ncols = len(values)
        elif len(values) != ncols:
            raise FormatError(
                f"{path}: inconsistent column count (row {line!r} has {len(values)}, expected {ncols})"
            )
        rows.append(values)

    if ncols is None or ncols < 2:
        raise FormatError(f"{path}: need at least two columns (x and y)")

    arr = np.asarray(rows, dtype=float)
    x = arr[:, 0]

    title_base = meta_overrides.pop("title", None) or os.path.splitext(os.path.basename(path))[0]
    x_unit = _XUNIT_BY_NAME.get((meta_overrides.pop("x_unit", None) or "").lower(), XUnit.WAVENUMBER)
    y_unit_override = meta_overrides.pop("y_unit", None)

    spectra = []
    n_series = ncols - 1
    for i in range(n_series):
        y = arr[:, i + 1]
        if n_series == 1:
            title = title_base
            y_unit_name = y_unit_override
        else:
            col_name = header_fields[i + 1] if header_fields and len(header_fields) > i + 1 else f"y{i + 1}"
            title = f"{title_base} [{col_name}]"
            y_unit_name = y_unit_override
        y_unit = _YUNIT_BY_NAME.get((y_unit_name or "").lower(), YUnit.ARBITRARY)
        spectra.append(
            Spectrum(
                x=x.copy(),
                y=y,
                x_unit=x_unit,
                y_unit=y_unit,
                title=title,
                meta={"source_format": "csv", **meta_overrides},
            )
        )
    return spectra


def write_csv(spectra: list[Spectrum], path: str | os.PathLike) -> None:
    path = os.fspath(path)
    if not spectra:
        raise FormatError("cannot write an empty list of spectra to CSV")

    x = spectra[0].x
    for s in spectra[1:]:
        if len(s) != len(x) or not np.allclose(s.x, x):
            raise FormatError(
                "cannot write multiple spectra with different x axes to one CSV file; "
                "write them to separate files instead"
            )

    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# title: {spectra[0].title}\n")
        fh.write(f"# x_unit: {spectra[0].x_unit.value}\n")
        if len(spectra) == 1:
            fh.write(f"# y_unit: {spectra[0].y_unit.value}\n")
        writer = csv.writer(fh)
        if len(spectra) == 1:
            writer.writerow(["x", "y"])
            for xv, yv in zip(x, spectra[0].y):
                writer.writerow([repr(float(xv)), repr(float(yv))])
        else:
            header = ["x"] + [s.title or f"y{i + 1}" for i, s in enumerate(spectra)]
            writer.writerow(header)
            cols = [s.y for s in spectra]
            for row_idx, xv in enumerate(x):
                writer.writerow([repr(float(xv))] + [repr(float(col[row_idx])) for col in cols])
