"""File I/O: reading and writing spectra in the formats OpenFTIR understands.

Public API::

    load(path) -> list[Spectrum]
    save(spectra, path, fmt=None) -> None
    SUPPORTED_READ: dict[str, str]    # extension -> human description
    SUPPORTED_WRITE: dict[str, str]
    class FormatError(ValueError)

Dispatch is purely by file-extension (case-insensitive), optionally
overridden by the ``fmt`` argument to :func:`save`.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Union

from openftir.spectrum import Spectrum

from . import _bsp, _csv, _jcamp, _ofjson, _spa
from .errors import FormatError

__all__ = ["load", "save", "SUPPORTED_READ", "SUPPORTED_WRITE", "FormatError"]

SUPPORTED_READ: dict[str, str] = {
    ".spa": "Thermo OMNIC single spectrum (SPA)",
    ".spg": "Thermo OMNIC spectra group (SPG)",
    ".bsp": "Varian Resolutions Pro spectra (BSP)",
    ".jdx": "JCAMP-DX spectrum",
    ".dx": "JCAMP-DX spectrum",
    ".csv": "Comma/tab/semicolon separated values",
    ".txt": "Delimited text (two or more columns)",
    ".ofjson": "OpenFTIR native spectrum (JSON)",
}

SUPPORTED_WRITE: dict[str, str] = {
    ".jdx": "JCAMP-DX spectrum",
    ".dx": "JCAMP-DX spectrum",
    ".csv": "Comma separated values",
    ".txt": "Delimited text",
    ".ofjson": "OpenFTIR native spectrum (JSON)",
}

_READERS = {
    ".spa": lambda p: [_spa.read_spa(p)],
    ".spg": _spa.read_spg,
    ".bsp": _bsp.read_bsp,
    ".jdx": _jcamp.read_jcamp,
    ".dx": _jcamp.read_jcamp,
    ".csv": _csv.read_csv,
    ".txt": _csv.read_csv,
    ".ofjson": _ofjson.read_ofjson,
}

_WRITERS = {
    ".jdx": _jcamp.write_jcamp,
    ".dx": _jcamp.write_jcamp,
    ".csv": _csv.write_csv,
    ".txt": _csv.write_csv,
    ".ofjson": _ofjson.write_ofjson,
}


def _ext(path: str | os.PathLike, fmt: str | None) -> str:
    if fmt:
        ext = fmt if fmt.startswith(".") else f".{fmt}"
        return ext.lower()
    return os.path.splitext(os.fspath(path))[1].lower()


def load(path: str | os.PathLike) -> list[Spectrum]:
    """Load one or more spectra from ``path``, dispatching on its extension."""
    path = os.fspath(path)
    ext = _ext(path, None)
    reader = _READERS.get(ext)
    if reader is None:
        known = ", ".join(sorted(SUPPORTED_READ))
        raise FormatError(f"{path}: unsupported file extension {ext!r} (supported: {known})")
    if not os.path.exists(path):
        raise FormatError(f"{path}: file does not exist")
    return reader(path)


def save(
    spectra: Union[Spectrum, Sequence[Spectrum]],
    path: str | os.PathLike,
    fmt: str | None = None,
) -> None:
    """Save one or more spectra to ``path``.

    ``fmt`` overrides extension-based format detection (e.g. to save a
    JCAMP-DX file with a ``.txt`` extension); it may be given with or
    without a leading dot.
    """
    path = os.fspath(path)
    if isinstance(spectra, Spectrum):
        spectra_list = [spectra]
    else:
        spectra_list = list(spectra)
        if not spectra_list:
            raise FormatError("save() requires at least one spectrum")
        if not all(isinstance(s, Spectrum) for s in spectra_list):
            raise FormatError("save() requires Spectrum instances")

    ext = _ext(path, fmt)
    writer = _WRITERS.get(ext)
    if writer is None:
        known = ", ".join(sorted(SUPPORTED_WRITE))
        raise FormatError(f"{path}: unsupported write format {ext!r} (supported: {known})")
    writer(spectra_list, path)
