"""Synthetic-spectrum helpers shared by the processing test modules."""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit


def lorentzian(x, center, height, width):
    return height / (1.0 + ((x - center) / (width / 2.0)) ** 2)


def gaussian(x, center, height, width):
    sigma = width / 2.3548
    return height * np.exp(-0.5 * ((x - center) / sigma) ** 2)


def make_absorbance_spectrum(
    n=1801,
    x_min=400.0,
    x_max=4000.0,
    bands=None,
    baseline_slope=0.0003,
    baseline_curve=0.0,
    noise=0.001,
    descending=True,
    seed=0,
):
    """A synthetic absorbance spectrum: Lorentzian bands + sloped/curved baseline + noise."""
    rng = np.random.default_rng(seed)
    x = np.linspace(x_max, x_min, n) if descending else np.linspace(x_min, x_max, n)

    if bands is None:
        bands = [(1050.0, 0.8, 20.0), (1650.0, 0.5, 15.0), (2900.0, 0.6, 25.0)]

    y = np.zeros_like(x)
    for center, height, width in bands:
        y += lorentzian(x, center, height, width)

    t = (x - x_min) / (x_max - x_min)
    y += baseline_slope * (x - x_min) + baseline_curve * (t - 0.5) ** 2 * (x_max - x_min)
    y += rng.normal(0.0, noise, size=x.shape)

    return Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title="synthetic", meta={"bands": bands})


def make_transmittance_spectrum(**kwargs):
    """Like make_absorbance_spectrum, but returns %T with dips instead of absorbance peaks."""
    s = make_absorbance_spectrum(**kwargs)
    t = np.power(10.0, -s.y) * 100.0
    return s.derive(t, "-> %T for test fixture", y_unit=YUnit.TRANSMITTANCE)
