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


# -- exporting several reports at once ------------------------------------------------


def _bulk_window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    for name in ("Aspirin", "Caffeine", "Unknown 3"):
        window.model.add(_make_spectrum(name), name=name)
    background = Spectrum(
        x=np.linspace(4000, 600, 500), y=np.ones(500), x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM
    )
    window.model.add(background, name="Background", visible=False)
    return window


def _run_bulk_dialog(monkeypatch, tmp_path, edit=None, untick=()):
    from PySide6.QtCore import Qt

    from openftir.gui.dialogs.bulk_report_dialog import BulkReportDialog

    seen = {}

    def fake_exec(self):
        seen["names"] = [self.table.item(i, 0).text() for i in range(self.table.rowCount())]
        for i, new_name in (edit or {}).items():
            self.table.item(i, 0).setText(new_name)
        for i in untick:
            self.table.item(i, 0).setCheckState(Qt.Unchecked)
        self.folder_edit.setText(str(tmp_path))
        return True

    monkeypatch.setattr(BulkReportDialog, "exec", fake_exec)
    return seen


def test_bulk_export_writes_one_report_per_ticked_sample(qtbot, tmp_path, monkeypatch):
    window = _bulk_window(qtbot)
    seen = _run_bulk_dialog(monkeypatch, tmp_path, edit={2: "Ibuprofen"}, untick=(1,))

    window._on_export_pdf_reports()

    # Backgrounds can't have a report, so they aren't offered.
    assert seen["names"] == ["Aspirin", "Caffeine", "Unknown 3"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Aspirin report.pdf", "Ibuprofen report.pdf"]
    # A name typed in the dialog renames the spectrum in the app too.
    assert [e.name for e in window.model.entries()][:3] == ["Aspirin", "Caffeine", "Ibuprofen"]


def test_bulk_export_keeps_samples_with_the_same_name_apart(qtbot, tmp_path, monkeypatch):
    window = _bulk_window(qtbot)
    _run_bulk_dialog(monkeypatch, tmp_path, edit={1: "Aspirin", 2: "Aspirin"})

    window._on_export_pdf_reports()

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Aspirin report (2).pdf",
        "Aspirin report (3).pdf",
        "Aspirin report.pdf",
    ]


def test_bulk_export_entry_in_the_simple_export_menu(qtbot, monkeypatch):
    window = _bulk_window(qtbot)
    called = []
    monkeypatch.setattr(window, "_on_export_pdf_reports", lambda: called.append(True))
    window.simple_bottom_bar.export_all_pdf_requested.disconnect()
    window.simple_bottom_bar.export_all_pdf_requested.connect(window._on_export_pdf_reports)

    menu = window.simple_bottom_bar.export_btn.menu()
    action = next(a for a in menu.actions() if "all samples" in a.text())
    action.trigger()
    assert called


def test_file_menu_items_act_on_the_selected_spectrum_not_the_first(qtbot, tmp_path, monkeypatch):
    # Regression: menu items pass their checked state (False) to the
    # handler, which treated it as row 0 -- usually the background -- so
    # File -> Export PDF Report complained it wasn't %Transmittance.
    from PySide6.QtGui import QAction

    from openftir import report

    window = MainWindow()
    qtbot.addWidget(window)
    # The background comes first, as in a normal session (collected first).
    background = Spectrum(
        x=np.linspace(4000, 600, 500), y=np.ones(500), x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM
    )
    window.model.add(background, name="Background", visible=False)
    sample_row = window.model.add(_make_spectrum("Aspirin"), name="Aspirin")
    window.list_view.setCurrentIndex(window.model.index(sample_row))

    exported = []
    monkeypatch.setattr(report, "export_report_pdf", lambda path, spectrum, **kw: exported.append(kw["title"]))
    _accept_report_dialog(monkeypatch)
    _stub_save_path(monkeypatch, tmp_path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: warnings.append(a))

    action = next(a for a in window.findChildren(QAction) if a.text() == "Export PDF &Report...")
    action.trigger()

    assert warnings == []
    assert exported == ["Aspirin"]
