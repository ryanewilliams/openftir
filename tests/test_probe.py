"""Tests for the read-only instrument discovery probe (``openftir.probe``),
the ``openftir probe`` CLI subcommand, and the GUI ``ProbeDialog``."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

from openftir.cli import main as cli_main
from openftir.probe import run_probe, write_report


def test_run_probe_returns_usb_and_network_sections():
    report = run_probe()

    assert "usb" in report
    assert "network" in report
    assert "usb_highlights" in report
    assert "network_highlights" in report
    assert "host" in report
    assert "generated_at" in report
    assert "optional_dependencies" in report

    # On this Linux CI box pyusb/psutil may or may not be installed, but the
    # probe must still report a method and never raise.
    assert report["usb"].get("method") in ("pyusb", "platform-tool-fallback")
    assert report["network"].get("method") in ("psutil", "platform-tool-fallback")


def test_run_probe_never_raises_with_bad_scan_host():
    # A polite TCP scan against an unreachable/invalid host must not raise --
    # it should just record failures in the per-port results.
    report = run_probe(scan_host="256.256.256.256", ports=[9999])
    assert "tcp_scan" in report
    assert report["tcp_scan"]["host"] == "256.256.256.256"
    assert len(report["tcp_scan"]["results"]) == 1


def test_write_report_writes_valid_json(tmp_path):
    report = run_probe()
    out_path = write_report(report, tmp_path)

    assert out_path.exists()
    assert out_path.parent == tmp_path
    loaded = json.loads(out_path.read_text())
    assert loaded["host"]["hostname"] == report["host"]["hostname"]


def test_cli_probe_subcommand_writes_report(tmp_path, capsys):
    code = cli_main(["probe", "--output-dir", str(tmp_path), "--quiet"])
    assert code == 0

    files = list(tmp_path.glob("instrument_probe_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert "usb" in data
    assert "network" in data


def test_cli_probe_subcommand_prints_summary(tmp_path, capsys):
    code = cli_main(["probe", "--output-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Report written to:" in out


def test_probe_dialog_run_completes(qtbot):
    from openftir.gui.dialogs.probe_dialog import ProbeDialog

    dialog = ProbeDialog()
    qtbot.addWidget(dialog)

    dialog.run_btn.click()
    qtbot.waitUntil(lambda: dialog._report is not None, timeout=10000)

    assert dialog.save_btn.isEnabled()
    assert dialog.copy_btn.isEnabled()
    assert "Generated:" in dialog.summary_view.toPlainText()


def test_probe_dialog_save_report(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from openftir.gui.dialogs.probe_dialog import ProbeDialog

    dialog = ProbeDialog()
    qtbot.addWidget(dialog)

    dialog.run_btn.click()
    qtbot.waitUntil(lambda: dialog._report is not None, timeout=10000)

    save_path = tmp_path / "report.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(save_path), ""))
    )
    dialog.save_btn.click()

    assert save_path.exists()
    data = json.loads(save_path.read_text())
    assert "usb" in data
