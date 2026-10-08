"""JCAMP-DX reader/writer (IR flavour).

Implements enough of the JCAMP-DX 5.01 / 6.00 spectroscopy-data standard to
round-trip IR spectra:

  * ``##XYDATA=(X++(Y..Y))`` in plain ASCII (AFFN) form and in the
    compressed ASCII-Squeezed-Difference-Form (ASDF): SQZ, DIF, DUP and
    DIFDUP, per the pseudo-digit tables of the JCAMP-DX spec (JCAMP-DX: A
    Standard Form for Exchange of Infrared Spectra, Appl. Spectrosc. 42
    (1988) 151; see also the IUPAC JCAMP-DX protocols).
  * ``##XYPOINTS=(XY..XY)`` plain (X, Y) pairs.
  * ``##XFACTOR``/``##YFACTOR``/``##FIRSTX``/``##LASTX``/``##NPOINTS``.
  * ``##XUNITS``/``##YUNITS`` (``1/CM``, ``MICROMETERS``, ``NANOMETERS``,
    ``ABSORBANCE``, ``TRANSMITTANCE``, ``REFLECTANCE``, ``KUBELKA-MUNK``,
    ``LOG(1/R)``, ``SINGLE BEAM``, ...).
  * Multi-block (``##BLOCKS=n`` / ``##DATA TYPE=LINK``) files: each child
    block (``##TITLE=`` ... ``##END=``) becomes one ``Spectrum``.

The writer only ever emits uncompressed AFFN ``X++(Y..Y)`` data (the task
only requires AFFN on write; ASDF is a read-side convenience for
interoperating with instrument-vendor exports).

Pseudo-digit tables (character -> (kind, signed value)):

    SQZ  (absolute value, replaces sign + leading digit + delimiter)
        '@'=+0 'A'=+1 'B'=+2 'C'=+3 'D'=+4 'E'=+5 'F'=+6 'G'=+7 'H'=+8 'I'=+9
        'a'=-1 'b'=-2 'c'=-3 'd'=-4 'e'=-5 'f'=-6 'g'=-7 'h'=-8 'i'=-9
    DIF  (difference from the previous Y value)
        '%'=+0 'J'=+1 'K'=+2 'L'=+3 'M'=+4 'N'=+5 'O'=+6 'P'=+7 'Q'=+8 'R'=+9
        'j'=-1 'k'=-2 'l'=-3 'm'=-4 'n'=-5 'o'=-6 'p'=-7 'q'=-8 'r'=-9
    DUP  (repeat the previous SQZ/DIF operation, total N occurrences)
        'S'=1 'T'=2 'U'=3 'V'=4 'W'=5 'X'=6 'Y'=7 'Z'=8 's'=9
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from .errors import FormatError

_SQZ = {
    "@": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8, "I": 9,
    "a": -1, "b": -2, "c": -3, "d": -4, "e": -5, "f": -6, "g": -7, "h": -8, "i": -9,
}
_DIF = {
    "%": 0, "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "O": 6, "P": 7, "Q": 8, "R": 9,
    "j": -1, "k": -2, "l": -3, "m": -4, "n": -5, "o": -6, "p": -7, "q": -8, "r": -9,
}
_DUP = {"S": 1, "T": 2, "U": 3, "V": 4, "W": 5, "X": 6, "Y": 7, "Z": 8, "s": 9}

_XUNITS_MAP = {
    "1/CM": XUnit.WAVENUMBER,
    "CM-1": XUnit.WAVENUMBER,
    "MICROMETERS": XUnit.WAVELENGTH_UM,
    "MICRONS": XUnit.WAVELENGTH_UM,
    "UM": XUnit.WAVELENGTH_UM,
    "NANOMETERS": XUnit.WAVELENGTH_NM,
    "NM": XUnit.WAVELENGTH_NM,
}
_XUNIT_TO_JCAMP = {
    XUnit.WAVENUMBER: "1/CM",
    XUnit.WAVELENGTH_UM: "MICROMETERS",
    XUnit.WAVELENGTH_NM: "NANOMETERS",
    XUnit.POINTS: "ARBITRARY UNITS",
}

_YUNITS_MAP = {
    "ABSORBANCE": YUnit.ABSORBANCE,
    "TRANSMITTANCE": YUnit.TRANSMITTANCE,
    "%TRANSMITTANCE": YUnit.TRANSMITTANCE,
    "REFLECTANCE": YUnit.REFLECTANCE,
    "%REFLECTANCE": YUnit.REFLECTANCE,
    "KUBELKA-MUNK": YUnit.KUBELKA_MUNK,
    "KUBELKAMUNK": YUnit.KUBELKA_MUNK,
    "LOG(1/R)": YUnit.LOG_INV_REFLECTANCE,
    "SINGLEBEAM": YUnit.SINGLE_BEAM,
    "SINGLE BEAM": YUnit.SINGLE_BEAM,
    "ARBITRARYUNITS": YUnit.ARBITRARY,
    "ARBITRARY UNITS": YUnit.ARBITRARY,
}
_YUNIT_TO_JCAMP = {
    YUnit.ABSORBANCE: "ABSORBANCE",
    YUnit.TRANSMITTANCE: "TRANSMITTANCE",
    YUnit.REFLECTANCE: "REFLECTANCE",
    YUnit.KUBELKA_MUNK: "KUBELKA-MUNK",
    YUnit.LOG_INV_REFLECTANCE: "LOG(1/R)",
    YUnit.SINGLE_BEAM: "SINGLE BEAM",
    YUnit.INTERFEROGRAM: "ARBITRARY UNITS",
    YUnit.ARBITRARY: "ARBITRARY UNITS",
}


def _normalize_label(label: str) -> str:
    return re.sub(r"[ _\-]", "", label.strip().upper())


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def _first_line(value: str) -> str:
    stripped = value.strip()
    lines = stripped.splitlines()
    return lines[0].strip() if lines else ""


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value) -> None:
        self.kind = kind
        self.value = value


def _tokenize_asdf_line(line: str) -> list[_Token]:
    tokens: list[_Token] = []
    sign = 1
    digits = ""
    pending_kind: str | None = None  # 'plain' | 'sqz' | 'dif'
    pending_lead: int = 0

    def flush():
        nonlocal pending_kind, digits, sign, pending_lead
        if pending_kind is not None:
            text = digits if digits else "0"
            try:
                mantissa = float(text)
            except ValueError:
                raise FormatError(f"malformed number {text!r} in JCAMP ASDF data line {line!r}")
            if pending_kind == "plain":
                tokens.append(_Token("value", sign * mantissa))
            elif pending_kind == "sqz":
                # the pseudo-digit supplies the leading digit; remaining
                # plain digits (if any) are appended after it.
                combined = str(pending_lead) if digits == "" else f"{pending_lead}{digits}"
                tokens.append(_Token("value", sign * float(combined)))
            elif pending_kind == "dif":
                combined = str(pending_lead) if digits == "" else f"{pending_lead}{digits}"
                tokens.append(_Token("diff", sign * float(combined)))
        pending_kind = None
        digits = ""
        sign = 1
        pending_lead = 0

    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c in _SQZ:
            flush()
            lead = _SQZ[c]
            pending_kind = "sqz"
            sign = -1 if lead < 0 else 1
            pending_lead = abs(lead)
            digits = ""
        elif c in _DIF:
            flush()
            lead = _DIF[c]
            pending_kind = "dif"
            sign = -1 if lead < 0 else 1
            pending_lead = abs(lead)
            digits = ""
        elif c in _DUP:
            flush()
            tokens.append(_Token("dup", _DUP[c]))
        elif c in "+-":
            flush()
            pending_kind = "plain"
            sign = -1 if c == "-" else 1
            digits = ""
        elif c.isdigit() or c == ".":
            if pending_kind is None:
                pending_kind = "plain"
                sign = 1
            digits += c
        elif c in " \t,":
            flush()
        else:
            raise FormatError(f"unexpected character {c!r} in JCAMP ASDF data line {line!r}")
        i += 1
    flush()
    return tokens


_FLOAT_TOKEN_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


def _try_plain_float_row(line: str) -> list[float] | None:
    """Fast path for a true AFFN line: plain (optionally exponential)
    floats separated only by whitespace/commas, with *no* ASDF pseudo-digit
    letters at all.

    This is what tells apart a plain line that happens to contain a
    scientific-notation exponent (``"4000 1.2e-05"``) from a genuinely
    ASDF-compressed line (``"2000C0K"``): if re-joining the matched float
    tokens reproduces the line exactly (modulo whitespace/commas), there is
    no leftover pseudo-digit letter and it is safe to parse as plain
    floats directly, bypassing the SQZ/DIF/DUP tokenizer entirely. Returns
    ``None`` when the line is not of this form, so the caller should fall
    back to :func:`_tokenize_asdf_line`.
    """
    tokens = _FLOAT_TOKEN_RE.findall(line)
    if not tokens:
        return None
    if re.sub(r"[\s,]", "", line) != "".join(tokens):
        return None
    try:
        return [float(t) for t in tokens]
    except ValueError:
        return None


def _decode_asdf_block(lines: list[str]) -> list[tuple[float, list[float]]]:
    """Decode a sequence of ``X++(Y..Y)`` lines into (x_check, [y...]) rows."""
    rows: list[tuple[float, list[float]]] = []
    y_running: float | None = None
    last_op: tuple[str, float] | None = None  # ('abs', v) or ('diff', d)
    for line in lines:
        if not line.strip():
            continue
        plain = _try_plain_float_row(line)
        if plain is not None:
            x_check, *ys = plain
            if ys:
                y_running = ys[-1]
                last_op = ("abs", y_running)
            rows.append((x_check, ys))
            continue
        tokens = _tokenize_asdf_line(line)
        if not tokens:
            continue
        if tokens[0].kind not in ("value",):
            raise FormatError(f"JCAMP data line does not start with a plain X value: {line!r}")
        x_check = tokens[0].value
        ys: list[float] = []
        for tok in tokens[1:]:
            if tok.kind == "value":
                y_running = tok.value
                ys.append(y_running)
                last_op = ("abs", y_running)
            elif tok.kind == "diff":
                if y_running is None:
                    raise FormatError("JCAMP DIF value with no preceding absolute Y value")
                y_running = y_running + tok.value
                ys.append(y_running)
                last_op = ("diff", tok.value)
            elif tok.kind == "dup":
                if last_op is None:
                    raise FormatError("JCAMP DUP with no preceding value to repeat")
                kind, val = last_op
                for _ in range(tok.value - 1):
                    if kind == "abs":
                        y_running = val
                    else:
                        y_running = (y_running if y_running is not None else 0.0) + val
                    ys.append(y_running)
        rows.append((x_check, ys))
    return rows


def _parse_xypoints(lines: list[str]) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    text = " ".join(lines)
    pairs = re.split(r"[;\n]+", text)
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        parts = [p for p in re.split(r"[,\s]+", pair) if p]
        if len(parts) != 2:
            continue
        xs.append(float(parts[0]))
        ys.append(float(parts[1]))
    return xs, ys


def _split_ldrs(text: str) -> list[tuple[str, str]]:
    """Split JCAMP text into a flat list of (raw_label, value) pairs.

    Continuation lines (not starting with ``##``) are appended, newline
    separated, to the value of the preceding LDR -- this is how
    ``XYDATA``/``XYPOINTS`` data tables are carried.
    """
    ldrs: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("##"):
            if "=" not in line:
                continue
            label, _, value = line[2:].partition("=")
            ldrs.append((label.strip(), value.strip()))
        elif line.strip().startswith("$$") or not line.strip():
            continue
        else:
            if ldrs:
                label, value = ldrs[-1]
                ldrs[-1] = (label, value + "\n" + line)
            # else: stray text before any LDR -- ignore
    return ldrs


def _ldrs_to_blocks(ldrs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split a flat LDR list into blocks on TITLE/END pairs.

    A LINK (``##BLOCKS=n``) file has an outer block whose body is just the
    block count, followed by n inner ``TITLE...END`` blocks. A plain file
    is just one block. Either way, every ``##TITLE=`` starts a new block
    and the matching ``##END=`` (same nesting depth) closes it; we treat
    blocks as non-nested for simplicity (true JCAMP nesting is rare for IR
    data) and collect every TITLE..END span whose body contains XYDATA or
    XYPOINTS.
    """
    blocks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] | None = None
    for label, value in ldrs:
        norm = _normalize_label(label)
        if norm == "TITLE":
            if current is not None:
                blocks.append(current)
            current = [(label, value)]
        elif current is not None:
            current.append((label, value))
            if norm == "END":
                blocks.append(current)
                current = None
    if current:
        blocks.append(current)
    # Keep only blocks that actually carry a data table -- this drops the
    # outer wrapper block of a LINK file (whose only content is BLOCKS=n).
    return [b for b in blocks if any(_normalize_label(l) in ("XYDATA", "XYPOINTS") for l, _ in b)]


def _block_to_spectrum(block: list[tuple[str, str]], path_for_errors: str) -> Spectrum:
    fields: dict[str, str] = {}
    data_label = None
    data_value = None
    title = ""
    for label, value in block:
        norm = _normalize_label(label)
        if norm == "TITLE":
            title = _first_line(value)
        elif norm in ("XYDATA", "XYPOINTS"):
            data_label = norm
            data_value = value
        elif norm in ("END", "BLOCKS"):
            continue
        else:
            fields[norm] = value
    if data_label is None or data_value is None:
        raise FormatError(f"{path_for_errors}: block {title!r} has no ##XYDATA or ##XYPOINTS")

    def ffield(name: str, default: float | None = None) -> float | None:
        if name in fields:
            first_line = _first_line(fields[name])
            if not first_line:
                return default
            try:
                return float(first_line)
            except ValueError as exc:
                raise FormatError(f"{path_for_errors}: bad numeric field ##{name}={first_line!r}") from exc
        return default

    xfactor = ffield("XFACTOR", 1.0)
    yfactor = ffield("YFACTOR", 1.0)
    firstx = ffield("FIRSTX")
    lastx = ffield("LASTX")
    npoints = fields.get("NPOINTS")

    data_lines = data_value.splitlines()
    if data_lines and "(" in data_lines[0]:
        # First line of the LDR value is the variable-list header, e.g.
        # "(X++(Y..Y))" -- already captured in data_label; discard it.
        _, *data_lines = data_lines

    if data_label == "XYDATA":
        rows = _decode_asdf_block(data_lines)
        y_values: list[float] = []
        for _x_check, ys in rows:
            y_values.extend(ys)
        y = np.asarray(y_values, dtype=float) * yfactor
        n = len(y)
        if firstx is None or lastx is None:
            raise FormatError(f"{path_for_errors}: XYDATA block is missing FIRSTX/LASTX")
        firstx *= xfactor
        lastx *= xfactor
        if n < 2:
            x = np.array([firstx] if n else [])
        else:
            x = np.linspace(firstx, lastx, n)
    else:  # XYPOINTS
        xs, ys = _parse_xypoints(data_lines)
        x = np.asarray(xs, dtype=float) * xfactor
        y = np.asarray(ys, dtype=float) * yfactor

    if npoints is not None:
        try:
            n_expected = int(float(_first_line(npoints)))
        except ValueError:
            n_expected = len(x)
        if n_expected != len(x):
            fields.setdefault("_npoints_mismatch", f"declared {n_expected}, decoded {len(x)}")

    xunits_raw = _first_line(fields.get("XUNITS", ""))
    yunits_raw = _first_line(fields.get("YUNITS", ""))
    x_unit = _XUNITS_MAP.get(_normalize_value(xunits_raw).replace(" ", ""), XUnit.WAVENUMBER)
    y_unit = _YUNITS_MAP.get(_normalize_value(yunits_raw), YUnit.ABSORBANCE)

    meta: dict = {"source_format": "jcamp-dx"}
    history: list[str] = []
    acquired: datetime | None = None
    for norm, value in fields.items():
        if norm in ("XUNITS", "YUNITS", "XFACTOR", "YFACTOR", "FIRSTX", "LASTX", "NPOINTS"):
            continue
        clean = value.strip()
        if norm == "HISTORY":
            history.extend(line for line in clean.splitlines() if line.strip())
            continue
        meta[norm.lower()] = _first_line(clean) if "\n" not in clean else clean

    return Spectrum(
        x=x,
        y=y,
        x_unit=x_unit,
        y_unit=y_unit,
        title=title,
        meta=meta,
        acquired=acquired,
        history=history,
    )


def read_jcamp(path: str | os.PathLike) -> list[Spectrum]:
    path = os.fspath(path)
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as fh:
            text = fh.read()

    if "##" not in text:
        raise FormatError(f"{path}: no JCAMP-DX labelled data records (##...=) found")

    ldrs = _split_ldrs(text)
    blocks = _ldrs_to_blocks(ldrs)
    if not blocks:
        raise FormatError(f"{path}: no ##XYDATA or ##XYPOINTS block found")

    spectra = []
    for block in blocks:
        spectra.append(_block_to_spectrum(block, path))
    return spectra


_LINE_WIDTH = 80


def _format_number(v: float) -> str:
    """Render ``v`` as plain fixed-point ASCII -- never exponent notation.

    JCAMP-DX AFFN data must be plain digits (``XYDATA`` with no SQZ/DIF/DUP
    letters); a Python ``repr()``/``str()`` float can fall back to
    scientific notation (``"1.2e-05"``), and that lowercase/uppercase ``e``
    is indistinguishable from an ASDF SQZ pseudo-digit (``e`` = SQZ -5) to
    a reader expecting compressed data, corrupting round-trips of small
    values. ``numpy.format_float_positional(..., unique=True)`` gives the
    shortest fixed-point string that parses back to the exact same double.
    """
    fv = float(v)
    if fv == 0.0:
        return "0"
    if fv.is_integer() and abs(fv) < 1e15:
        return str(int(fv))
    if not np.isfinite(fv):
        return repr(fv)
    return np.format_float_positional(fv, unique=True, trim="0")


def _write_block(spectrum: Spectrum, fh) -> None:
    x = spectrum.x
    y = spectrum.y
    n = len(spectrum)
    firstx = float(x[0]) if n else 0.0
    lastx = float(x[-1]) if n else 0.0

    fh.write(f"##TITLE={spectrum.title}\n")
    fh.write("##JCAMP-DX=5.01\n")
    fh.write("##DATA TYPE=INFRARED SPECTRUM\n")
    fh.write(f"##ORIGIN=OpenFTIR\n")
    fh.write(f"##OWNER=\n")
    if spectrum.acquired is not None:
        fh.write(f"##LONGDATE={spectrum.acquired.strftime('%Y/%m/%d')}\n")
        fh.write(f"##TIME={spectrum.acquired.strftime('%H:%M:%S')}\n")
    for key, value in spectrum.meta.items():
        if key == "source_format":
            continue
        label = str(key).upper()
        text = str(value).replace("\n", " ")
        fh.write(f"##{label}={text}\n")
    if spectrum.history:
        fh.write(f"##HISTORY={' | '.join(spectrum.history)}\n")

    fh.write(f"##XUNITS={_XUNIT_TO_JCAMP.get(spectrum.x_unit, '1/CM')}\n")
    fh.write(f"##YUNITS={_YUNIT_TO_JCAMP.get(spectrum.y_unit, 'ARBITRARY UNITS')}\n")
    fh.write("##XFACTOR=1\n")
    fh.write("##YFACTOR=1\n")
    fh.write(f"##FIRSTX={_format_number(firstx)}\n")
    fh.write(f"##LASTX={_format_number(lastx)}\n")
    if n > 1:
        fh.write(f"##DELTAX={_format_number((lastx - firstx) / (n - 1))}\n")
    fh.write(f"##NPOINTS={n}\n")
    miny = float(np.min(y)) if n else 0.0
    maxy = float(np.max(y)) if n else 0.0
    fh.write(f"##MINY={_format_number(miny)}\n")
    fh.write(f"##MAXY={_format_number(maxy)}\n")

    fh.write("##XYDATA=(X++(Y..Y))\n")
    i = 0
    while i < n:
        xv_text = _format_number(float(x[i]))
        parts = [xv_text]
        line_len = len(xv_text)
        j = i
        while j < n:
            yv_text = _format_number(float(y[j]))
            addition = 1 + len(yv_text)  # separating space + the value itself
            if line_len + addition > _LINE_WIDTH and j > i:
                break
            parts.append(yv_text)
            line_len += addition
            j += 1
        fh.write(" ".join(parts) + "\n")
        i = j
    fh.write("##END=\n")


def write_jcamp(spectra: list[Spectrum], path: str | os.PathLike) -> None:
    path = os.fspath(path)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        if len(spectra) == 1:
            _write_block(spectra[0], fh)
        else:
            fh.write(f"##TITLE=OpenFTIR export ({len(spectra)} spectra)\n")
            fh.write("##JCAMP-DX=5.01\n")
            fh.write("##DATA TYPE=LINK\n")
            fh.write(f"##BLOCKS={len(spectra)}\n")
            for spectrum in spectra:
                _write_block(spectrum, fh)
            fh.write("##END=\n")
