"""Tests for the CSV/TXT reader/writer."""

from __future__ import annotations

import numpy as np
import pytest

from openftir.io import FormatError, load, save
from openftir.io._csv import read_csv, write_csv
from openftir.spectrum import Spectrum, XUnit, YUnit


def _make_spectrum(n=25):
    x = np.linspace(4000.0, 400.0, n)
    y = np.linspace(0.0, 1.0, n)
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title="csv roundtrip")


def test_csv_roundtrip(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.csv"
    write_csv([s], path)
    out = read_csv(path)
    assert len(out) == 1
    assert out[0].title == "csv roundtrip"
    assert out[0].x_unit == XUnit.WAVENUMBER
    assert out[0].y_unit == YUnit.ABSORBANCE
    np.testing.assert_allclose(out[0].x, s.x)
    np.testing.assert_allclose(out[0].y, s.y)


def test_csv_roundtrip_via_dispatcher(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.txt"
    save(s, path, fmt="csv")
    out = load(path)
    np.testing.assert_allclose(out[0].y, s.y)


def test_csv_comma_delimited_no_header(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("4000,0.1\n3000,0.2\n2000,0.3\n")
    out = read_csv(path)
    np.testing.assert_allclose(out[0].x, [4000.0, 3000.0, 2000.0])
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])


def test_csv_semicolon_delimited(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("x;y\n4000;0,1\n3000;0,2\n2000;0,3\n")
    out = read_csv(path)
    np.testing.assert_allclose(out[0].x, [4000.0, 3000.0, 2000.0])
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])


def test_csv_tab_delimited(tmp_path):
    path = tmp_path / "tabs.tsv"
    # extension doesn't matter for read_csv() called directly
    path.write_text("4000\t0.1\n3000\t0.2\n2000\t0.3\n")
    out = read_csv(path)
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])


def test_csv_whitespace_delimited(tmp_path):
    path = tmp_path / "ws.txt"
    path.write_text("4000   0.1\n3000   0.2\n2000   0.3\n")
    out = read_csv(path)
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])


def test_csv_decimal_comma(tmp_path):
    path = tmp_path / "decimalcomma.csv"
    # semicolon-delimited with comma as decimal separator (European locale)
    path.write_text("x;y\n4000;0,123\n3000;0,456\n")
    out = read_csv(path)
    np.testing.assert_allclose(out[0].y, [0.123, 0.456])


def test_csv_multi_column_multiple_spectra(tmp_path):
    path = tmp_path / "multi.csv"
    path.write_text("x,sample_a,sample_b\n4000,0.1,0.9\n3000,0.2,0.8\n2000,0.3,0.7\n")
    out = read_csv(path)
    assert len(out) == 2
    np.testing.assert_allclose(out[0].y, [0.1, 0.2, 0.3])
    np.testing.assert_allclose(out[1].y, [0.9, 0.8, 0.7])
    assert "sample_a" in out[0].title
    assert "sample_b" in out[1].title


def test_csv_write_multi_column(tmp_path):
    x = np.linspace(4000, 400, 10)
    s1 = Spectrum(x=x, y=np.linspace(0, 1, 10), title="a")
    s2 = Spectrum(x=x, y=np.linspace(1, 0, 10), title="b")
    path = tmp_path / "out.csv"
    write_csv([s1, s2], path)
    out = read_csv(path)
    assert len(out) == 2
    np.testing.assert_allclose(out[0].y, s1.y)
    np.testing.assert_allclose(out[1].y, s2.y)


def test_csv_write_mismatched_x_raises(tmp_path):
    s1 = Spectrum(x=np.linspace(4000, 400, 10), y=np.zeros(10))
    s2 = Spectrum(x=np.linspace(4000, 400, 11), y=np.zeros(11))
    path = tmp_path / "bad.csv"
    with pytest.raises(FormatError):
        write_csv([s1, s2], path)


def test_csv_single_column_raises(tmp_path):
    path = tmp_path / "onecol.csv"
    path.write_text("4000\n3000\n2000\n")
    with pytest.raises(FormatError):
        read_csv(path)


def test_csv_inconsistent_columns_raises(tmp_path):
    path = tmp_path / "ragged.csv"
    path.write_text("4000,0.1\n3000,0.2,0.3\n")
    with pytest.raises(FormatError):
        read_csv(path)
