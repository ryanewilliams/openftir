"""PDF report export entry points: File menu action (both modes) and the
Simple-mode bottom bar's Export dropdown -- both must end up calling
``openftir.report.export_report_pdf`` with the selected entry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QMessageBox

from openftir.gui import file_dialogs
from openftir.gui.dialogs import ReportDialog
from openftir.gui.main_window import MainWindow
from openftir.spectrum import Spectrum, XUnit, YUnit


def _make_spectrum(title="demo") -> Spectrum:
    x = np.linspace(4000, 600, 500)
    y = 100.0 - 10.0 * np.exp(-((x - 1700.0) ** 2) / (2 * 30.0**2))
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.TRANSMITTANCE, title=title)


def _accept_report_dialog(monkeypatch):
    monkeypatch.setattr(ReportDialog, "exec", lambda self: True)


def _stub_save_path(monkeypatch, tmp_path, name="Sample report.pdf"):
    path = str(tmp_path / name)
    monkeypatch.setattr(file_dialogs, "get_save_path", lambda *a, **k: path)
    return path


def test_export_pdf_report_action_exists_in_both_modes(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    file_menu_actions = [a.text() for a in window.findChildren(type(window.simple_mode_action))]
    assert any("Export PDF" in text for text in file_menu_actions)

    # The action is reachable (and wired the same way) regardless of which
    # mode is currently showing -- the menu bar is shared by both modes.
    window._apply_mode("simple")
    assert window._mode == "simple"
    file_menu_actions_simple = [a.text() for a in window.findChildren(type(window.simple_mode_action))]
    assert any("Export PDF" in text for text in file_menu_actions_simple)

    window._apply_mode("advanced")
    assert window._mode == "advanced"
    file_menu_actions_after = [a.text() for a in window.findChildren(type(window.simple_mode_action))]
    assert any("Export PDF" in text for text in file_menu_actions_after)


def test_file_menu_export_report_calls_exporter_with_selected_entry(qtbot, tmp_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum("to-export"), name="to-export")
    window.list_view.setCurrentIndex(window.model.index(row))

    _accept_report_dialog(monkeypatch)
    out_path = _stub_save_path(monkeypatch, tmp_path)

    calls = []

    def fake_export(path, spectrum, **kwargs):
        calls.append((path, spectrum, kwargs))

    monkeypatch.setattr("openftir.gui.main_window.report.export_report_pdf", fake_export)

    window._on_export_pdf_report()

    assert len(calls) == 1
    path, spectrum, kwargs = calls[0]
    assert path == out_path
    assert spectrum is window.model.entry_at(row).spectrum
    assert kwargs["title"] == "to-export"
    assert "manual_peaks" in kwargs
    assert "auto_peak_params" in kwargs
    assert kwargs["paper"] == "letter"


def test_simple_bottom_bar_export_pdf_entry_calls_same_handler(qtbot, tmp_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum("chip-sample"), name="chip-sample")
    window.list_view.setCurrentIndex(window.model.index(row))

    _accept_report_dialog(monkeypatch)
    out_path = _stub_save_path(monkeypatch, tmp_path, "chip-sample report.pdf")

    calls = []
    monkeypatch.setattr(
        "openftir.gui.main_window.report.export_report_pdf",
        lambda path, spectrum, **kwargs: calls.append((path, spectrum, kwargs)),
    )

    window.simple_bottom_bar.export_pdf_requested.emit()

    assert len(calls) == 1
    assert calls[0][0] == out_path


def test_export_report_without_a_selection_shows_a_message(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.model.rowCount() == 0

    calls = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: calls.append(a))

    window._on_export_pdf_report()
    assert len(calls) == 1


def test_export_report_rejects_single_beam_spectrum(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    x = np.linspace(4000, 600, 50)
    y = np.ones_like(x)
    row = window.model.add(
        Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM, title="raw"),
        name="raw",
        visible=False,
    )
    window.list_view.setCurrentIndex(window.model.index(row))

    calls = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: calls.append(a))

    window._on_export_pdf_report()
    assert len(calls) == 1
