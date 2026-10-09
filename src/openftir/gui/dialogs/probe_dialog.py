"""Dialog for running the read-only instrument discovery probe.

See ``openftir.probe`` for what the probe actually does (and does not do --
it never sends any command to the instrument). This dialog just runs it on a
background thread (via ``CallWorker``) so the GUI never blocks, shows a
human-readable summary, and lets the user save the raw JSON report or copy a
text summary to the clipboard.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from openftir.gui import file_dialogs
from openftir.gui.workers import CallWorker, start_worker

_EXPLANATION = (
    "This tool only lists USB devices and network interfaces on this computer; "
    "it sends nothing to the spectrometer.\n\n"
    "For the clearest result, run it once with the spectrometer unplugged, then "
    "again with it plugged in and switched on, and compare the two reports."
)


def _summary_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    host = report.get("host", {})
    lines.append(f"Generated: {report.get('generated_at', '?')}")
    lines.append(f"Host: {host.get('hostname', '?')}  ({host.get('platform', '?')})")
    lines.append("")

    deps = report.get("optional_dependencies", {})
    lines.append(
        f"Optional dependencies: pyusb={'yes' if deps.get('pyusb') else 'no'}, "
        f"psutil={'yes' if deps.get('psutil') else 'no'}"
    )
    lines.append("")

    usb_info = report.get("usb", {})
    devices = usb_info.get("devices", []) or []
    lines.append(
        f"USB enumeration method: {usb_info.get('method', '?')} "
        f"(backend: {usb_info.get('backend', 'n/a')})"
    )
    if usb_info.get("method") == "pyusb":
        lines.append(f"  {len(devices)} USB device(s) found")
    else:
        lines.append("  (raw platform-tool output saved in the JSON report)")
    lines.append("")

    from openftir.probe import format_thermo_matches

    thermo_matches = report.get("usb_thermo_matches") or []
    if thermo_matches:
        lines.append(f"Thermo/Nicolet instrument(s) found ({len(thermo_matches)}):")
        for block in format_thermo_matches(thermo_matches):
            lines.append(block)
            lines.append("")
    else:
        lines.append("No Thermo/Nicolet USB device identified.")
        usb_highlights = report.get("usb_highlights") or []
        if usb_highlights:
            lines.append("Weaker USB highlights (string matches only -- inspect the raw report):")
            for h in usb_highlights:
                lines.append(f"  - {h}")
    lines.append("")

    net_info = report.get("network", {})
    interfaces = net_info.get("interfaces", {}) or {}
    lines.append(f"Network enumeration method: {net_info.get('method', '?')}")
    if interfaces:
        lines.append(f"  {len(interfaces)} network interface(s): {', '.join(interfaces)}")
    lines.append("")

    net_highlights = report.get("network_highlights") or []
    if net_highlights:
        lines.append("RNDIS/CDC-ECM/Thermo-looking network matches:")
        for h in net_highlights:
            lines.append(f"  - {h}")
    else:
        lines.append("No RNDIS/CDC-ECM/Thermo-looking network interfaces found.")

    tcp_scan = report.get("tcp_scan")
    if tcp_scan:
        lines.append("")
        lines.append(f"TCP connect-scan of {tcp_scan.get('host')}:")
        for r in tcp_scan.get("results", []):
            state = "OPEN" if r.get("open") else "closed/filtered"
            lines.append(f"  port {r.get('port'):>5}: {state}")

    return "\n".join(lines)


class ProbeDialog(QDialog):
    """Runs ``openftir.probe.run_probe`` on a worker thread and shows a summary."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Probe Instrument Connection")
        self.setMinimumSize(560, 480)

        self._report: dict[str, Any] | None = None
        self._worker: CallWorker | None = None

        layout = QVBoxLayout(self)

        explanation = QLabel(_EXPLANATION)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._on_run)
        layout.addWidget(self.run_btn)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setPlaceholderText("Click Run to scan this computer's USB devices and network interfaces.")
        layout.addWidget(self.summary_view)

        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save report...")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        self.copy_btn = QPushButton("Copy to clipboard")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._on_copy)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # -- actions ------------------------------------------------------------------

    def _on_run(self) -> None:
        from openftir.probe import run_probe

        self.run_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.summary_view.setPlainText("Running probe...")

        worker = CallWorker(run_probe)
        worker.succeeded.connect(self._on_probe_done)
        worker.failed.connect(self._on_probe_failed)
        self._worker = start_worker(worker)

    def _on_probe_done(self, report: dict) -> None:
        self._report = report
        try:
            text = _summary_text(report)
        except Exception as exc:  # noqa: BLE001 - never crash the dialog
            text = f"Probe completed, but the summary could not be rendered:\n{exc}"
        self.summary_view.setPlainText(text)
        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def _on_probe_failed(self, message: str) -> None:
        self._report = None
        self.summary_view.setPlainText(f"Probe failed to run:\n{message}")
        self.run_btn.setEnabled(True)

    def _on_save(self) -> None:
        if self._report is None:
            return
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        default_name = f"instrument_probe_{timestamp}.json"
        path = file_dialogs.get_save_path(
            self, "Save Probe Report", default_name, "JSON (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._report, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save Report", f"Could not save report:\n{exc}")
            return
        self.summary_view.append(f"\nSaved report to: {path}")

    def _on_copy(self) -> None:
        if self._report is None:
            return
        try:
            text = _summary_text(self._report)
        except Exception as exc:  # noqa: BLE001
            text = f"(summary unavailable: {exc})"
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
