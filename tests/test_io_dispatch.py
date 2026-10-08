"""Tests for the top-level openftir.io.load/save dispatcher."""

from __future__ import annotations

import numpy as np
import pytest

from openftir.io import SUPPORTED_READ, SUPPORTED_WRITE, FormatError, load, save
from openftir.spectrum import Spectrum


def test_supported_dicts_cover_expected_extensions():
    for ext in (".spa", ".spg", ".jdx", ".dx", ".csv", ".txt", ".ofjson"):
        assert ext in SUPPORTED_READ
    for ext in (".jdx", ".dx", ".csv", ".txt", ".ofjson"):
        assert ext in SUPPORTED_WRITE
    # read-only formats must not claim to be writable
    assert ".spa" not in SUPPORTED_WRITE
    assert ".spg" not in SUPPORTED_WRITE


def test_load_unknown_extension_raises(tmp_path):
    path = tmp_path / "file.xyz"
    path.write_text("whatever")
    with pytest.raises(FormatError):
        load(path)


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FormatError):
        load(tmp_path / "does_not_exist.csv")


def test_save_unknown_extension_raises(tmp_path):
    s = Spectrum(x=np.array([1.0, 2.0]), y=np.array([1.0, 2.0]))
    with pytest.raises(FormatError):
        save(s, tmp_path / "file.xyz")


def test_save_single_spectrum_accepted_without_list(tmp_path):
    s = Spectrum(x=np.linspace(4000, 400, 10), y=np.zeros(10), title="solo")
    path = tmp_path / "solo.ofjson"
    save(s, path)
    out = load(path)
    assert len(out) == 1
    assert out[0].title == "solo"


def test_save_fmt_overrides_extension(tmp_path):
    s = Spectrum(x=np.linspace(4000, 400, 10), y=np.zeros(10), title="forced")
    path = tmp_path / "forced.txt"
    save(s, path, fmt="jdx")
    text = path.read_text()
    assert "##XYDATA" in text


def test_save_empty_list_raises(tmp_path):
    with pytest.raises(FormatError):
        save([], tmp_path / "empty.csv")
