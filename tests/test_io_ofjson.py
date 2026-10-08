"""Tests for the native .ofjson format."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from openftir.io import FormatError, load, save
from openftir.io._ofjson import read_ofjson, write_ofjson
from openftir.spectrum import Spectrum, XUnit, YUnit


def _make_spectrum():
    x = np.linspace(4000.0, 400.0, 30)
    y = np.sin(x / 150.0)
    return Spectrum(
        x=x,
        y=y,
        x_unit=XUnit.WAVENUMBER,
        y_unit=YUnit.KUBELKA_MUNK,
        title="ofjson roundtrip",
        meta={"resolution": 4.0, "scans": 32, "nested": {"gain": "auto"}},
        acquired=datetime(2026, 10, 1, 15, 48, 51),
        history=["collected", "baseline corrected"],
    )


def test_ofjson_full_roundtrip(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.ofjson"
    write_ofjson([s], path)
    out = read_ofjson(path)
    assert len(out) == 1
    o = out[0]
    assert o.title == s.title
    assert o.x_unit == s.x_unit
    assert o.y_unit == s.y_unit
    assert o.meta == s.meta
    assert o.acquired == s.acquired
    assert o.history == s.history
    np.testing.assert_array_equal(o.x, s.x)
    np.testing.assert_array_equal(o.y, s.y)


def test_ofjson_roundtrip_via_dispatcher(tmp_path):
    s = _make_spectrum()
    path = tmp_path / "s.ofjson"
    save(s, path)
    out = load(path)
    np.testing.assert_array_equal(out[0].y, s.y)
    assert out[0].acquired == s.acquired


def test_ofjson_multiple_spectra(tmp_path):
    s1 = _make_spectrum()
    s2 = _make_spectrum().copy(title="second", acquired=None, history=[])
    path = tmp_path / "multi.ofjson"
    write_ofjson([s1, s2], path)
    out = read_ofjson(path)
    assert [o.title for o in out] == ["ofjson roundtrip", "second"]
    assert out[1].acquired is None
    assert out[1].history == []


def test_ofjson_no_acquired(tmp_path):
    s = _make_spectrum().copy(acquired=None)
    path = tmp_path / "noacq.ofjson"
    write_ofjson([s], path)
    out = read_ofjson(path)
    assert out[0].acquired is None


def test_ofjson_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.ofjson"
    path.write_text("{not valid json")
    with pytest.raises(FormatError):
        read_ofjson(path)


def test_ofjson_unrecognised_document_raises(tmp_path):
    path = tmp_path / "wrong.ofjson"
    path.write_text('{"hello": "world"}')
    with pytest.raises(FormatError):
        read_ofjson(path)


def test_ofjson_unknown_unit_raises(tmp_path):
    path = tmp_path / "badunit.ofjson"
    path.write_text(
        '{"spectra": [{"x": [1,2], "y": [1,2], "x_unit": "not-a-unit", "y_unit": "absorbance"}]}'
    )
    with pytest.raises(FormatError):
        read_ofjson(path)
