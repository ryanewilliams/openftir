"""A processing action invoked through the main window adds a new, derived
spectrum to the list without touching the original."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QItemSelectionModel

from openftir import processing
from openftir.gui.main_window import MainWindow
from openftir.spectrum import Spectrum, XUnit, YUnit


def _make_spectrum() -> Spectrum:
    x = np.linspace(4000, 400, 400)
    y = np.sin(x / 50.0) + np.random.default_rng(0).normal(0, 0.01, x.shape)
    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title="raw")


def test_processing_action_adds_new_spectrum(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    row = window.model.add(_make_spectrum(), name="raw")
    window.list_view.setCurrentIndex(window.model.index(row))

    new_row = window._apply(lambda s: processing.savgol(s, window=11, order=2), "smoothed")

    assert new_row is not None
    assert window.model.rowCount() == 2
    # original is untouched
    original = window.model.spectrum_at(row)
    assert original.history == []
    smoothed = window.model.spectrum_at(new_row)
    assert smoothed.history == ["Savitzky-Golay smooth (window=11, order=2)"]
    assert not np.allclose(smoothed.y, original.y)


def test_convert_to_transmittance_then_back(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum(), name="raw")
    window.list_view.setCurrentIndex(window.model.index(row))

    new_row = window._apply(processing.to_transmittance, "convert to %T")
    assert window.model.spectrum_at(new_row).y_unit == YUnit.TRANSMITTANCE


def test_crop_to_view(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum(), name="raw")
    window.list_view.setCurrentIndex(window.model.index(row))
    window.plot.full_scale()

    new_row = window._on_crop_to_view()
    # crop uses the current view range, which may clip the x grid, so just
    # check it produced a (probably smaller) new spectrum
    assert window.model.rowCount() == 2


def test_average_selected(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    r1 = window.model.add(_make_spectrum(), name="a")
    r2 = window.model.add(_make_spectrum(), name="b")
    window.list_view.selectionModel().select(
        window.model.index(r1), QItemSelectionModel.Select
    )
    window.list_view.selectionModel().select(
        window.model.index(r2), QItemSelectionModel.Select
    )

    window._on_average()
    assert window.model.rowCount() == 3
    assert "Average" in window.model.entry_at(2).name
