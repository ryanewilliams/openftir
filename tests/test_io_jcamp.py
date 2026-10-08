"""Tests for the JCAMP-DX reader/writer."""

from __future__ import annotations

import numpy as np
import pytest

from openftir.io import FormatError, load, save
from openftir.io._jcamp import _decode_asdf_block, read_jcamp, write_jcamp
from openftir.spectrum import Spectrum, XUnit, YUnit

# --- ASDF decoding, against hand-worked examples from the JCAMP-DX spec ---
# (JCAMP-DX: A Standard Form for the Exchange of Infrared Spectra, Appl.
# Spectrosc. 42 (1988) 151; the SQZ/DIF/DUP pseudo-digit tables therein.)


def test_sqz_decode():
    # "30, 32" squeezed: delimiter+leading-digit+sign replaced by a
    # pseudo-digit: C(=+3)+'0' -> 30, C(=+3)+'2' -> 32.
    rows = _decode_asdf_block(["2000C0C2"])
    assert rows == [(2000.0, [30.0, 32.0])]


def test_dif_decode():
    # Same values via DIF: first value SQZ'd (30), second is a difference
    # of +2 from the first (encoded as DIF digit K=+2) -> 32.
    rows = _decode_asdf_block(["2000C0K"])
    assert rows == [(2000.0, [30.0, 32.0])]


def test_dup_decode():
    # 50 50 50 50 as SQZ+DUP: E(=+5)+'0' -> 50 (one occurrence), then
    # V(=4) means that value occurs 4 times in total.
    rows = _decode_asdf_block(["50E0V"])
    assert rows == [(50.0, [50.0, 50.0, 50.0, 50.0])]


def test_difdup_decode():
    # A difference of 0 repeated: first value is 50 (SQZ), then DIF '%'=0
    # (no change) repeated via DUP T(=2) -> two more zero-diffs -> three
    # identical values total.
    rows = _decode_asdf_block(["50E0%T"])
    assert rows == [(50.0, [50.0, 50.0, 50.0])]


def test_negative_sqz_and_dif():
    # a = SQZ -1 (i.e. "-1..."), j = DIF -1.
    rows = _decode_asdf_block(["0a5j2"])
    # 'a' -> sign -, lead digit 1, then plain digit '5' appended -> -15
    # 'j' -> DIF sign -, lead digit 1, plain digit '2' appended -> diff -12
    assert rows[0][0] == 0.0
    assert rows[0][1][0] == pytest.approx(-15.0)
    assert rows[0][1][1] == pytest.approx(-15.0 - 12.0)


def test_multirow_dif_carries_running_value():
    rows = _decode_asdf_block(["0C0", "2J"])
    # row1: X=0, Y=30 (SQZ). row2: X=2, Y = running(30) + DIF(+1*10?) ...
    # Simpler: just check the running value carried from row1 into row2.
    assert rows[0] == (0.0, [30.0])
    assert rows[1][0] == 2.0
    assert rows[1][1][0] == pytest.approx(31.0)


# --- AFFN plain-number decoding ---


def test_affn_plain_decode():
    rows = _decode_asdf_block(["4000 0.1 0.2 0.3"])
    assert rows[0][0] == pytest.approx(4000.0)
    assert rows[0][1] == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]


# --- Full file round trips ---


def _make_spectrum(n=40, title="roundtrip", y_unit=YUnit.ABSORBANCE):
    x = np.linspace(4000.0, 400.0, n)
    y = np.sin(x / 123.0) * 0.5 + 0.3
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=y_unit, title=title, meta={"resolution": "4"})


def test_jcamp_single_block_roundtrip(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.jdx"
    write_jcamp([s], path)
    out = read_jcamp(path)
    assert len(out) == 1
    assert out[0].title == "roundtrip"
    assert out[0].x_unit == XUnit.WAVENUMBER
    assert out[0].y_unit == YUnit.ABSORBANCE
    np.testing.assert_allclose(out[0].x, s.x, rtol=1e-6)
    np.testing.assert_allclose(out[0].y, s.y, atol=1e-6)


def test_jcamp_multiblock_roundtrip(tmp_path):
    s1 = _make_spectrum(title="first", y_unit=YUnit.ABSORBANCE)
    s2 = _make_spectrum(title="second", y_unit=YUnit.TRANSMITTANCE)
    path = tmp_path / "multi.jdx"
    write_jcamp([s1, s2], path)
    out = read_jcamp(path)
    assert [o.title for o in out] == ["first", "second"]
    assert out[1].y_unit == YUnit.TRANSMITTANCE
    np.testing.assert_allclose(out[0].y, s1.y, atol=1e-6)
    np.testing.assert_allclose(out[1].y, s2.y, atol=1e-6)


def test_jcamp_roundtrip_via_dispatcher(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.dx"
    save(s, path)
    out = load(path)
    np.testing.assert_allclose(out[0].y, s.y, atol=1e-6)


def test_jcamp_xyunits_variants(tmp_path):
    text = """##TITLE=units test
##JCAMP-DX=5.01
##XUNITS=MICROMETERS
##YUNITS=%Transmittance
##FIRSTX=2.5
##LASTX=25
##XFACTOR=1
##YFACTOR=1
##NPOINTS=3
##XYDATA=(X++(Y..Y))
2.5 10 20 30
##END=
"""
    path = tmp_path / "units.jdx"
    path.write_text(text)
    out = read_jcamp(path)
    assert out[0].x_unit == XUnit.WAVELENGTH_UM
    assert out[0].y_unit == YUnit.TRANSMITTANCE
    np.testing.assert_allclose(out[0].y, [10.0, 20.0, 30.0])


def test_jcamp_xypoints(tmp_path):
    text = """##TITLE=xy points
##JCAMP-DX=5.01
##XUNITS=1/CM
##YUNITS=ABSORBANCE
##XFACTOR=1
##YFACTOR=1
##XYPOINTS=(XY..XY)
4000,0.1; 3000,0.2; 2000,0.3
##END=
"""
    path = tmp_path / "xy.jdx"
    path.write_text(text)
    out = read_jcamp(path)
    np.testing.assert_allclose(out[0].x, [4000.0, 3000.0, 2000.0])
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])


def test_jcamp_xfactor_yfactor_applied(tmp_path):
    text = """##TITLE=factors
##JCAMP-DX=5.01
##XUNITS=1/CM
##YUNITS=ABSORBANCE
##FIRSTX=400
##LASTX=800
##XFACTOR=2
##YFACTOR=0.5
##NPOINTS=3
##XYDATA=(X++(Y..Y))
200 2 4 6
##END=
"""
    path = tmp_path / "factors.jdx"
    path.write_text(text)
    out = read_jcamp(path)
    # FIRSTX/LASTX (400/800) scaled by XFACTOR=2 -> real range 800..1600.
    np.testing.assert_allclose(out[0].x, [800.0, 1200.0, 1600.0])
    np.testing.assert_allclose(out[0].y, [1.0, 2.0, 3.0])


def test_jcamp_missing_xydata_raises(tmp_path):
    text = "##TITLE=broken\n##JCAMP-DX=5.01\n##END=\n"
    path = tmp_path / "broken.jdx"
    path.write_text(text)
    with pytest.raises(FormatError):
        read_jcamp(path)


def test_jcamp_no_ldrs_raises(tmp_path):
    path = tmp_path / "notjcamp.jdx"
    path.write_text("just some random text\nwith no LDRs at all\n")
    with pytest.raises(FormatError):
        read_jcamp(path)


# --- Regression test: writer must never emit exponent notation, and the
# reader must not mistake a plain-AFFN exponent's 'e'/'E' for an ASDF SQZ
# pseudo-digit (SQZ 'e' = -5, 'E' = +5). A wide dynamic range (1e-6..1e2,
# both signs) reliably produces repr() values in scientific notation. ---


def test_jcamp_roundtrip_wide_dynamic_range(tmp_path):
    rng = np.random.default_rng(42)
    n = 3600
    x = np.linspace(4000.0, 400.0, n)
    magnitude = 10.0 ** rng.uniform(-6, 2, size=n)
    sign = rng.choice([-1.0, 1.0], size=n)
    y = magnitude * sign

    s = Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title="wide range")
    path = tmp_path / "wide.jdx"
    write_jcamp([s], path)

    text = path.read_text()
    data_section = text.split("##XYDATA=(X++(Y..Y))", 1)[1].split("##END=", 1)[0]
    assert "e" not in data_section and "E" not in data_section, "writer must avoid exponent notation"
    for line in data_section.strip().splitlines():
        assert len(line) <= 80, f"JCAMP-DX 5.01 lines must be <=80 chars, got {len(line)}: {line!r}"

    out = read_jcamp(path)
    assert len(out) == 1
    assert len(out[0]) == n, "point count must round-trip exactly (no spurious splits from 'e')"
    np.testing.assert_allclose(out[0].x, x)
    rel_err = np.abs((out[0].y - y) / y)
    assert rel_err.max() < 1e-8


def test_jcamp_reader_accepts_affn_exponent_notation(tmp_path):
    """Even a hand-written AFFN line using scientific notation must parse
    as plain floats, not be mistaken for ASDF SQZ/DIF/DUP pseudo-digits."""
    text = """##TITLE=exponent test
##JCAMP-DX=5.01
##XUNITS=1/CM
##YUNITS=ABSORBANCE
##FIRSTX=4000
##LASTX=3998
##XFACTOR=1
##YFACTOR=1
##NPOINTS=3
##XYDATA=(X++(Y..Y))
4000 1.5e-05 -2.3E+02 6.0e0
##END=
"""
    path = tmp_path / "exp.jdx"
    path.write_text(text)
    out = read_jcamp(path)
    assert len(out[0]) == 3
    np.testing.assert_allclose(out[0].y, [1.5e-05, -230.0, 6.0])
