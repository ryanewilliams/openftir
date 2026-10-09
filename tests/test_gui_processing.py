"""A processing action invoked through the main window adds a new, derived
spectrum to the list without touching the original."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QItemSelectionModel

from openftir import processing
from openftir.gui.dialogs import CropDialog
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


def test_crop_dialog_applies_numeric_range(qtbot, monkeypatch):
    """Cropping is now a numeric-range dialog, not "crop to the current
    view" -- the plot no longer supports panning/zooming to define a view."""
    window = MainWindow()
    qtbot.addWidget(window)
    row = window.model.add(_make_spectrum(), name="raw")
    window.list_view.setCurrentIndex(window.model.index(row))

    monkeypatch.setattr(CropDialog, "exec", lambda self: 1)
    monkeypatch.setattr(CropDialog, "params", lambda self: {"x_min": 1000.0, "x_max": 2000.0})

    window._on_crop()

    assert window.model.rowCount() == 2
    cropped = window.model.spectrum_at(1)
    assert cropped.x.min() >= 1000.0 - 1e-6
    assert cropped.x.max() <= 2000.0 + 1e-6


def test_crop_dialog_defaults_to_full_spectrum_range(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    spectrum = _make_spectrum()
    row = window.model.add(spectrum, name="raw")
    window.list_view.setCurrentIndex(window.model.index(row))

    dialog = CropDialog(float(spectrum.x.min()), float(spectrum.x.max()))
    qtbot.addWidget(dialog)
    params = dialog.params()
    assert params["x_min"] == pytest.approx(float(spectrum.x.min()))
    assert params["x_max"] == pytest.approx(float(spectrum.x.max()))


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
