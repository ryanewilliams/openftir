"""Varian Resolutions Pro .bsp reader.

The unit tests build synthetic spectrum streams in the layout the reader
expects (no real lab files in the repo). Set OPENFTIR_REAL_BSP to a folder
of real .bsp files to also check those.
"""

import os
import struct
from pathlib import Path

import numpy as np
import pytest

from openftir.io import _bsp
from openftir.io.errors import FormatError
from openftir.spectrum import XUnit, YUnit


def _data_block(values, first, dx):
    block = bytearray(64)
    block[0:8] = struct.pack("<I", 4) + b"Data"
    struct.pack_into("<d", block, _bsp._DX, dx)
    struct.pack_into("<i", block, _bsp._FIRST, first)
    struct.pack_into("<I", block, _bsp._COUNT, len(values))
    struct.pack_into("<I", block, _bsp._NBYTES, len(values) * 8)
    return bytes(block) + np.asarray(values, "<f8").tobytes()


def _text(s):
    b = s.encode("latin-1")
    return struct.pack("<II", len(b), len(b)) + b


def _prop(name, value):
    v = value.encode("latin-1")
    return _text(name) + _bsp._PROP_PREFIX + struct.pack("<4I", len(v), 4, len(v), len(v)) + v


def _stream(values, first, dx, y_label, props, interferogram=None):
    parts = [struct.pack("<I", 3), _data_block(values, first, dx)]
    parts.append(struct.pack("<I", 5) + b"Parms" + b"\0" * 16 + _text("Wavenumber") + _text(y_label))
    parts.append(_text("Properties") + b"".join(_prop(k, v) for k, v in props.items()))
    if interferogram is not None:
        parts.append(_text("Interferogram") + _data_block(interferogram, -3, 6.3e-5))
    return b"".join(parts)


def test_transmittance_spectrum_axis_units_and_settings():
    y = np.linspace(99.0, 101.0, 50)
    stream = _stream(y, 622, 1.9288, "%Transmittance", {
        "Time Stamp": "Wednesday, October 14, 2026 14:57:09",
        "Resolution": "4",
        "Scans": "4",
        "Effective Laser Wavenumber": "15800.8235",
    })
    s = _bsp.parse_spectrum_stream(stream)
    assert s.y_unit == YUnit.TRANSMITTANCE and s.x_unit == XUnit.WAVENUMBER
    np.testing.assert_allclose(s.x[[0, -1]], [622 * 1.9288, 671 * 1.9288])
    np.testing.assert_array_equal(s.y, y)
    assert s.meta["resolution"] == 4.0 and s.meta["scans"] == 4
    assert s.meta["laser_wavenumber"] == pytest.approx(15800.8235)
    assert s.acquired.isoformat() == "2026-10-14T14:57:09"
    assert s.title == "Sample"


def test_background_is_a_single_beam_and_uses_background_scans():
    stream = _stream(np.ones(10), 0, 1.9288, "Response", {"Scans": "0", "Background Scans": "16"})
    s = _bsp.parse_spectrum_stream(stream)
    assert s.y_unit == YUnit.SINGLE_BEAM
    assert s.meta["scans"] == 16
    assert s.title == "Background"


def test_raw_interferogram_is_available():
    ifg = np.array([0.1, -0.2, 0.9, -0.3, 0.05])
    stream = _stream(np.ones(4), 0, 1.0, "Response", {}, interferogram=ifg)
    s = _bsp.parse_spectrum_stream(stream, interferogram=True)
    assert s.y_unit == YUnit.INTERFEROGRAM
    np.testing.assert_array_equal(s.y, ifg)
    np.testing.assert_array_equal(s.x, [-3, -2, -1, 0, 1])
    assert s.meta["zpd_index"] == 3


def test_sample_comment_becomes_the_title():
    stream = _stream(np.ones(3), 0, 1.0, "Absorbance", {"Sample Comment": "Limonene, NaCl plates"})
    s = _bsp.parse_spectrum_stream(stream)
    assert s.title == "Limonene, NaCl plates" and s.y_unit == YUnit.ABSORBANCE


def test_corrupt_block_is_a_format_error():
    stream = bytearray(_stream(np.ones(8), 0, 1.0, "Response", {}))
    struct.pack_into("<I", stream, 4 + _bsp._NBYTES, 9999)  # size no longer matches the count
    with pytest.raises(FormatError):
        _bsp.parse_spectrum_stream(bytes(stream))


def test_not_an_ole_file_is_a_format_error(tmp_path):
    p = tmp_path / "x.bsp"
    p.write_bytes(b"not a compound document")
    with pytest.raises(FormatError):
        _bsp.read_bsp(p)


REAL = os.environ.get("OPENFTIR_REAL_BSP")


@pytest.mark.skipif(not REAL, reason="set OPENFTIR_REAL_BSP to a folder of real .bsp files")
def test_real_bsp_files():
    from openftir import io

    files = sorted(Path(REAL).glob("*.bsp"))
    assert files
    for f in files:
        spectra = io.load(f)
        units = [s.y_unit for s in spectra]
        assert YUnit.SINGLE_BEAM in units, f
        sample = next(s for s in spectra if s.y_unit != YUnit.SINGLE_BEAM)
        assert sample.acquired is not None and sample.meta["resolution"]
        ifgs = [s for s in _bsp.read_bsp(f, interferograms=True) if s.y_unit == YUnit.INTERFEROGRAM]
        assert len(ifgs) == 2, f
