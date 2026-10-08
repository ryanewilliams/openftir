"""OpenFTIR native lossless JSON format (``.ofjson``).

This is the app's own "save project spectrum" format, so it is defined
entirely by this codebase (no external spec to match). The on-disk shape
is a single JSON object::

    {
      "openftir_ofjson_version": 1,
      "spectra": [
        {
          "title": "...",
          "x": [...], "y": [...],
          "x_unit": "cm-1", "y_unit": "absorbance",
          "meta": {...},
          "acquired": "2026-10-01T22:48:46" | null,
          "history": ["...", ...]
        },
        ...
      ]
    }

Every field on ``Spectrum`` round-trips exactly (modulo JSON's float
formatting, which is lossless for IEEE-754 doubles via ``repr``/``float``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from openftir.spectrum import Spectrum, XUnit, YUnit

from .errors import FormatError

_VERSION = 1


def _spectrum_to_dict(s: Spectrum) -> dict:
    return {
        "title": s.title,
        "x": s.x.tolist(),
        "y": s.y.tolist(),
        "x_unit": s.x_unit.value,
        "y_unit": s.y_unit.value,
        "meta": s.meta,
        "acquired": s.acquired.isoformat() if s.acquired is not None else None,
        "history": list(s.history),
    }


def _dict_to_spectrum(d: dict, path_for_errors: str) -> Spectrum:
    try:
        x = d["x"]
        y = d["y"]
    except KeyError as exc:
        raise FormatError(f"{path_for_errors}: spectrum entry missing {exc.args[0]!r}") from exc

    try:
        x_unit = XUnit(d.get("x_unit", XUnit.WAVENUMBER.value))
    except ValueError as exc:
        raise FormatError(f"{path_for_errors}: unknown x_unit {d.get('x_unit')!r}") from exc
    try:
        y_unit = YUnit(d.get("y_unit", YUnit.ARBITRARY.value))
    except ValueError as exc:
        raise FormatError(f"{path_for_errors}: unknown y_unit {d.get('y_unit')!r}") from exc

    acquired_raw = d.get("acquired")
    acquired = datetime.fromisoformat(acquired_raw) if acquired_raw else None

    return Spectrum(
        x=x,
        y=y,
        x_unit=x_unit,
        y_unit=y_unit,
        title=d.get("title", ""),
        meta=dict(d.get("meta", {})),
        acquired=acquired,
        history=list(d.get("history", [])),
    )


def read_ofjson(path: str | os.PathLike) -> list[Spectrum]:
    path = os.fspath(path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except json.JSONDecodeError as exc:
            raise FormatError(f"{path}: not valid JSON ({exc})") from exc

    if isinstance(doc, dict) and "spectra" in doc:
        entries = doc["spectra"]
    elif isinstance(doc, dict) and "x" in doc and "y" in doc:
        entries = [doc]
    elif isinstance(doc, list):
        entries = doc
    else:
        raise FormatError(f"{path}: not a recognised .ofjson document")

    if not entries:
        raise FormatError(f"{path}: .ofjson document has no spectra")

    return [_dict_to_spectrum(e, path) for e in entries]


def write_ofjson(spectra: list[Spectrum], path: str | os.PathLike) -> None:
    path = os.fspath(path)
    doc = {
        "openftir_ofjson_version": _VERSION,
        "spectra": [_spectrum_to_dict(s) for s in spectra],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
