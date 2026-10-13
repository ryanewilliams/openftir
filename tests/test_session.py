import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

import numpy as np
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from openftir import io as ftio
from openftir.gui.main_window import MainWindow
from openftir.session import Session, SessionEntry, load_session, save_session
from openftir.spectrum import Spectrum, XUnit, YUnit


def _sample(depth=10.0):
    x = np.linspace(4000, 600, 400)
    y = 100.0 - depth * np.exp(-((x - 1700.0) ** 2) / (2 * 30.0**2))
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.TRANSMITTANCE, acquired=datetime(2026, 10, 13, 14, 5))


def _background():
    x = np.linspace(4000, 600, 400)
    return Spectrum(x=x, y=np.ones_like(x), x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM)


def test_session_round_trip(tmp_path):
    bg_time = datetime(2026, 10, 13, 14, 0)
    session = Session(
        entries=[
            SessionEntry(_background(), "Background 14:00:00", "#888888", False),
            SessionEntry(_sample(), "Aspirin", "#d62728", True, [1700.0]),
            SessionEntry(_sample(30), "Caffeine", "#1f77b4", False),
        ],
        background=0,
        background_time=bg_time,
        selected=1,
    )
    path = tmp_path / "lab.ofjson"
    save_session(session, path)

    back = load_session(path)
    assert [e.name for e in back.entries] == ["Background 14:00:00", "Aspirin", "Caffeine"]
    assert [e.color for e in back.entries] == ["#888888", "#d62728", "#1f77b4"]
    assert [e.visible for e in back.entries] == [False, True, False]
    assert back.entries[1].manual_peaks == [1700.0]
    assert back.background == 0 and back.background_time == bg_time and back.selected == 1
    np.testing.assert_array_equal(back.entries[2].spectrum.y, session.entries[2].spectrum.y)
    # Still a normal spectra file for anything that doesn't know sessions.
    assert len(ftio.load(path)) == 3


def test_plain_ofjson_is_not_a_session(tmp_path):
    path = tmp_path / "plain.ofjson"
    ftio.save([_sample()], path)
    assert load_session(path) is None


def _populated_window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    bg = _background()
    window.model.add(bg, name="Background 14:00:00", visible=False)
    window.collect_controller.restore_background(bg, datetime(2026, 10, 13, 14, 0))
    row = window.model.add(_sample(), name="Sample 4")
    window.model.set_manual_peaks(row, [1700.0])
    window.model.add(_sample(30), name="Caffeine")
    window.list_view.setCurrentIndex(window.model.index(row))
    return window


def test_save_and_reopen_session_in_the_app(qtbot, tmp_path, monkeypatch):
    from openftir.gui import file_dialogs

    window = _populated_window(qtbot)
    path = tmp_path / "session.ofjson"
    monkeypatch.setattr(file_dialogs, "get_save_path", lambda *a, **k: str(path))
    window._on_save_session()
    assert path.exists()

    other = MainWindow()
    qtbot.addWidget(other)
    other._open_paths([str(path)])

    assert [e.name for e in other.model.entries()] == ["Background 14:00:00", "Sample 4", "Caffeine"]
    assert other.model.entry_at(1).manual_peaks == [1700.0]
    # The background is usable again, with its original age.
    assert other.collect_controller.background is other.model.entry_at(0).spectrum
    assert other.collect_controller.background_time == datetime(2026, 10, 13, 14, 0)
    assert other._current_row() == 1
    # New samples carry on numbering after the restored "Sample 4".
    assert other.collect_controller.next_sample_name() == "Sample 5"


def test_opening_a_session_with_spectra_open_can_add_to_them(qtbot, tmp_path, monkeypatch):
    window = _populated_window(qtbot)
    path = tmp_path / "s.ofjson"
    save_session(window._current_session(), path)

    def click(label):
        monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(
            QMessageBox, "clickedButton", lambda self: next(b for b in self.buttons() if b.text() == label)
        )

    click("Add to open spectra")
    window._open_paths([str(path)])
    assert window.model.rowCount() == 6

    click("Replace")
    window._open_paths([str(path)])
    assert window.model.rowCount() == 3


def test_autosave_and_restore_on_next_start(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "autosave_path", staticmethod(lambda: tmp_path / "autosave.ofjson"))
    window = MainWindow(autosave=True)
    qtbot.addWidget(window)
    window.model.add(_sample(), name="Unsaved work")
    qtbot.waitUntil(lambda: (tmp_path / "autosave.ofjson").exists(), timeout=10000)

    later = MainWindow(autosave=True)
    qtbot.addWidget(later)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.defaultButton())  # "Restore"
    later.offer_to_restore_autosave()
    assert [e.name for e in later.model.entries()] == ["Unsaved work"]


def test_closing_everything_clears_the_autosave(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "autosave_path", staticmethod(lambda: tmp_path / "autosave.ofjson"))
    window = MainWindow(autosave=True)
    qtbot.addWidget(window)
    window.model.add(_sample(), name="x")
    window._write_autosave()
    assert (tmp_path / "autosave.ofjson").exists()
    window.model.clear()
    window._write_autosave()
    assert not (tmp_path / "autosave.ofjson").exists()


def test_colours_survive_a_session(qtbot, tmp_path):
    from openftir.gui.spectrum_list import ColorRole

    window = _populated_window(qtbot)
    window.model.setData(window.model.index(2), QColor("#123456"), ColorRole)
    path = tmp_path / "c.ofjson"
    save_session(window._current_session(), path)
    other = MainWindow()
    qtbot.addWidget(other)
    other._open_paths([str(path)])
    assert other.model.entry_at(2).color.name() == "#123456"
