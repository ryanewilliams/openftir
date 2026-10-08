"""Tests for the ``openftir`` command-line interface (``openftir.cli.main``)."""

from __future__ import annotations

import io as _io
import sys
from contextlib import redirect_stdout

import numpy as np
import pytest

from openftir import io as ftio
from openftir.cli import main
from openftir.spectrum import Spectrum, XUnit, YUnit


def run_cli(args, stdin_text: str = ""):
    """Run ``main(args)`` capturing stdout; returns (exit_code, stdout_text)."""
    buf = _io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = _io.StringIO(stdin_text)
    try:
        with redirect_stdout(buf):
            code = main(args)
    finally:
        sys.stdin = old_stdin
    return code, buf.getvalue()


def make_spectrum(title="test", y_unit=YUnit.ABSORBANCE, n=1801, descending=True):
    x = np.linspace(4000.0, 400.0, n) if descending else np.linspace(400.0, 4000.0, n)
    y = np.zeros(n)
    # a few Lorentzian-ish bands so baseline/smooth/peaks have something to do
    for center, height, width in ((1601.0, 0.5, 10.0), (1493.0, 0.6, 10.0), (698.0, 0.7, 10.0)):
        y += height / (1.0 + ((x - center) / (width / 2.0)) ** 2)
    y += 0.05  # small offset baseline
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=y_unit, title=title)


# -- drivers / info -------------------------------------------------------------


def test_drivers_lists_simulated():
    code, out = run_cli(["drivers"])
    assert code == 0
    assert "simulated" in out


def test_info_prints_instrument_details():
    code, out = run_cli(["info", "--driver", "simulated"])
    assert code == 0
    assert "OpenFTIR Project" in out
    assert "diagnostics ok: True" in out


def test_info_unknown_driver_errors_cleanly(capsys):
    code, _out = run_cli(["info", "--driver", "nope"])
    assert code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


# -- collect ----------------------------------------------------------------------


def test_collect_simulated_no_prompt(tmp_path):
    out_path = tmp_path / "result.ofjson"
    code, _out = run_cli(
        [
            "collect",
            "--driver",
            "simulated",
            "--driver-opt",
            "seed=1",
            "--scans",
            "8",
            "--resolution",
            "8",
            "--no-prompt",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    assert out_path.exists()

    [spectrum] = ftio.load(str(out_path))
    assert spectrum.y_unit == YUnit.ABSORBANCE
    assert spectrum.x.size > 0


def test_collect_with_background_file(tmp_path):
    bg_path = tmp_path / "bg.ofjson"
    background = make_spectrum(y_unit=YUnit.SINGLE_BEAM)
    background = background.copy(y=np.abs(background.y) + 1.0)  # single-beam must stay positive
    ftio.save([background], str(bg_path))

    out_path = tmp_path / "result.jdx"
    code, _out = run_cli(
        [
            "collect",
            "--driver",
            "simulated",
            "--background",
            str(bg_path),
            "--scans",
            "4",
            "--resolution",
            "8",
            "--no-prompt",
            "--units",
            "transmittance",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    assert out_path.exists()
    [spectrum] = ftio.load(str(out_path))
    assert spectrum.y_unit == YUnit.TRANSMITTANCE


def test_collect_prompts_unless_no_prompt(tmp_path, monkeypatch):
    out_path = tmp_path / "result.ofjson"
    prompted = {"called": False}

    def fake_input(prompt=""):
        prompted["called"] = True
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    code, _out = run_cli(
        [
            "collect",
            "--driver",
            "simulated",
            "--scans",
            "4",
            "--resolution",
            "16",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    assert prompted["called"]


# -- convert ----------------------------------------------------------------------


def test_convert_single_spectrum(tmp_path):
    src = tmp_path / "in.ofjson"
    ftio.save([make_spectrum()], str(src))
    dst = tmp_path / "out.csv"

    code, _out = run_cli(["convert", str(src), str(dst)])
    assert code == 0
    assert dst.exists()
    [loaded] = ftio.load(str(dst))
    assert loaded.x.size == 1801


def test_convert_multi_spectrum_writes_suffixed_files(tmp_path):
    src = tmp_path / "multi.ofjson"
    ftio.save([make_spectrum(title="a"), make_spectrum(title="b")], str(src))
    dst = tmp_path / "out.csv"

    code, _out = run_cli(["convert", str(src), str(dst)])
    assert code == 0
    assert (tmp_path / "out_0.csv").exists()
    assert (tmp_path / "out_1.csv").exists()


def test_convert_multi_spectrum_with_index(tmp_path):
    src = tmp_path / "multi.ofjson"
    ftio.save([make_spectrum(title="a"), make_spectrum(title="b")], str(src))
    dst = tmp_path / "out.csv"

    code, _out = run_cli(["convert", str(src), str(dst), "--index", "1"])
    assert code == 0
    assert dst.exists()
    assert not (tmp_path / "out_0.csv").exists()


def test_convert_missing_file_errors_cleanly(tmp_path, capsys):
    code, _out = run_cli(["convert", str(tmp_path / "nope.ofjson"), str(tmp_path / "out.csv")])
    assert code == 1
    assert "error:" in capsys.readouterr().err


# -- info-file ----------------------------------------------------------------------


def test_info_file(tmp_path):
    src = tmp_path / "s.ofjson"
    ftio.save([make_spectrum(title="hello")], str(src))
    code, out = run_cli(["info-file", str(src)])
    assert code == 0
    assert "hello" in out
    assert "points:  1801" in out


# -- process ----------------------------------------------------------------------


def test_process_chain(tmp_path):
    src = tmp_path / "s.ofjson"
    ftio.save([make_spectrum()], str(src))
    dst = tmp_path / "processed.ofjson"

    code, _out = run_cli(
        [
            "process",
            str(src),
            "--baseline",
            "als",
            "--smooth",
            "7",
            "--normalize",
            "max",
            "--crop",
            "4000",
            "400",
            "-o",
            str(dst),
        ]
    )
    assert code == 0
    [result] = ftio.load(str(dst))
    assert np.max(np.abs(result.y)) <= 1.0001


def test_process_atr_requires_absorbance_like(tmp_path):
    src = tmp_path / "s.ofjson"
    ftio.save([make_spectrum(y_unit=YUnit.ABSORBANCE)], str(src))
    dst = tmp_path / "out.ofjson"
    code, _out = run_cli(["process", str(src), "--atr", "-o", str(dst)])
    assert code == 0
    assert dst.exists()


# -- peaks ----------------------------------------------------------------------


def test_peaks_table(tmp_path):
    src = tmp_path / "s.ofjson"
    ftio.save([make_spectrum()], str(src))
    code, out = run_cli(["peaks", str(src), "--prominence", "0.1"])
    assert code == 0
    assert "position" in out

    positions = [float(line.split()[0]) for line in out.splitlines()[1:] if line.strip()]
    assert len(positions) == 3
    for expected in (1601.0, 1493.0, 698.0):
        nearest = min(positions, key=lambda p: abs(p - expected))
        assert abs(nearest - expected) <= 5.0


# -- library / search ----------------------------------------------------------------------


def test_library_create_add_search(tmp_path):
    lib_dir = tmp_path / "lib"
    code, _out = run_cli(["library", "create", str(lib_dir)])
    assert code == 0
    assert (lib_dir / "index.json").exists()

    spec_path = tmp_path / "polystyrene.ofjson"
    ftio.save([make_spectrum(title="polystyrene")], str(spec_path))

    code, out = run_cli(["library", "add", str(lib_dir), str(spec_path)])
    assert code == 0
    assert "added 1 spectra" in out

    code, out = run_cli(["search", str(spec_path), "--library", str(lib_dir), "--top", "3"])
    assert code == 0
    assert "polystyrene" in out


def test_search_against_empty_library(tmp_path):
    lib_dir = tmp_path / "lib"
    run_cli(["library", "create", str(lib_dir)])
    spec_path = tmp_path / "s.ofjson"
    ftio.save([make_spectrum()], str(spec_path))
    code, out = run_cli(["search", str(spec_path), "--library", str(lib_dir)])
    assert code == 0
    assert "no matches" in out
