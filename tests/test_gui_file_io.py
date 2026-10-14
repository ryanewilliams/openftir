"""Loading files through the File menu plumbing adds spectra to the list."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QMessageBox

from openftir import io
from openftir.gui.main_window import MainWindow
from openftir.spectrum import Spectrum, XUnit, YUnit


def _make_spectrum(title="demo") -> Spectrum:
    x = np.linspace(4000, 400, 200)
    y = np.sin(x / 100.0)
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title=title)


def test_open_paths_adds_to_list(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)

    path = tmp_path / "demo.ofjson"
    io.save([_make_spectrum("demo")], str(path))

    window._open_paths([str(path)])

    assert window.model.rowCount() == 1
    entry = window.model.entry_at(0)
    assert entry.spectrum.y_unit == YUnit.ABSORBANCE
    assert len(entry.spectrum) == 200


def test_open_paths_reports_bad_file_without_crashing(qtbot, tmp_path, monkeypatch):
    # QMessageBox.critical() would otherwise open a modal dialog and hang the
    # (headless) test; replace it with a no-op so we can assert it was called.
    calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: calls.append(a))

    window = MainWindow()
    qtbot.addWidget(window)

    bad_path = tmp_path / "not_a_spectrum.csv"
    bad_path.write_text("this is not spectral data\nat all\n")

    # Must not raise -- errors are surfaced via QMessageBox, not an exception.
    window._open_paths([str(bad_path)])
    assert window.model.rowCount() == 0
    assert len(calls) == 1


def test_save_selected_round_trips(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum("to-save"), name="to-save")
    window.list_view.setCurrentIndex(window.model.index(row))

    out_path = tmp_path / "out.ofjson"
    entry = window.model.entry_at(row)
    io.save([entry.spectrum], str(out_path))

    loaded = io.load(str(out_path))
    assert len(loaded) == 1
    assert np.allclose(loaded[0].x, entry.spectrum.x)


def test_background_in_a_multi_spectrum_file_opens_hidden(qtbot, tmp_path, monkeypatch):
    # e.g. a Resolutions Pro .bsp holding the background and the sample.
    import numpy as np

    from openftir import io as io_mod
    from openftir.gui.main_window import MainWindow
    from openftir.spectrum import Spectrum, YUnit

    x = np.linspace(4000, 1200, 50)
    background = Spectrum(x=x, y=np.ones(50), y_unit=YUnit.SINGLE_BEAM, title="run - background")
    sample = Spectrum(x=x, y=np.full(50, 90.0), y_unit=YUnit.TRANSMITTANCE, title="run - sample")
    monkeypatch.setattr(io_mod, "load", lambda path: [background, sample])
    window = MainWindow()
    qtbot.addWidget(window)
    window._open_paths([str(tmp_path / "run.bsp")])
    entries = window.model.entries()
    assert [e.name for e in entries] == ["run - background", "run - sample"]
    assert [e.visible for e in entries] == [False, True]
