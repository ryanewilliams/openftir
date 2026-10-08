"""Unit conversions: Y axis (ratio/absorbance/transmittance/Kubelka-Munk) and
X axis (wavenumber <-> wavelength).
"""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from ._utils import interp_values

__all__ = ["ratio", "to_absorbance", "to_transmittance", "kubelka_munk", "convert_x"]

_PERCENT_UNITS = (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE)


def ratio(sample_sb: Spectrum, background_sb: Spectrum, to: YUnit = YUnit.TRANSMITTANCE) -> Spectrum:
    """Ratio a sample single-beam spectrum against a background single-beam spectrum.

    The background is linearly interpolated onto the sample's x axis if the
    two axes differ. ``to`` selects the result unit: ``YUnit.TRANSMITTANCE``
    (percent) or ``YUnit.ABSORBANCE``.
    """
    if to not in (YUnit.TRANSMITTANCE, YUnit.ABSORBANCE):
        raise ValueError("to must be YUnit.TRANSMITTANCE or YUnit.ABSORBANCE")

    bg_y = interp_values(sample_sb.x, background_sb.x, background_sb.y)
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = sample_sb.y / bg_y
    frac = np.where(np.isfinite(frac), frac, 0.0)

    if to == YUnit.TRANSMITTANCE:
        y = frac * 100.0
        step = "ratioed against background -> %T"
    else:
        t = np.clip(frac, 1e-8, None)
        y = -np.log10(t)
        step = "ratioed against background -> absorbance"

    return sample_sb.derive(y, step, y_unit=to)


def to_absorbance(s: Spectrum) -> Spectrum:
    """Convert a %T (or %R) spectrum to absorbance (``A = -log10(T_fraction)``).

    A spectrum already in absorbance is returned unchanged (as a copy).
    """
    if s.y_unit == YUnit.ABSORBANCE:
        return s.copy()
    if s.y_unit not in _PERCENT_UNITS:
        raise ValueError(f"cannot convert {s.y_unit!r} to absorbance; expected %T or %R")

    t = np.clip(s.y / 100.0, 1e-8, None)
    y = -np.log10(t)
    return s.derive(y, f"{s.y_unit.value} -> absorbance", y_unit=YUnit.ABSORBANCE)


def to_transmittance(s: Spectrum) -> Spectrum:
    """Convert an absorbance spectrum to %T (``T% = 10**(-A) * 100``).

    A spectrum already in transmittance is returned unchanged (as a copy).
    """
    if s.y_unit == YUnit.TRANSMITTANCE:
        return s.copy()
    if s.y_unit != YUnit.ABSORBANCE:
        raise ValueError(f"cannot convert {s.y_unit!r} to transmittance; expected absorbance")

    y = np.power(10.0, -s.y) * 100.0
    return s.derive(y, "absorbance -> %T", y_unit=YUnit.TRANSMITTANCE)


def kubelka_munk(s: Spectrum) -> Spectrum:
    """Convert a diffuse-reflectance (%R) spectrum to Kubelka-Munk units.

    ``KM = (1 - R)**2 / (2 * R)`` with ``R`` as a 0-1 fraction. Only
    conversion from ``YUnit.REFLECTANCE`` is supported; a spectrum already in
    Kubelka-Munk units is returned unchanged.
    """
    if s.y_unit == YUnit.KUBELKA_MUNK:
        return s.copy()
    if s.y_unit != YUnit.REFLECTANCE:
        raise ValueError(f"cannot convert {s.y_unit!r} to Kubelka-Munk; expected %R (REFLECTANCE)")

    r = np.clip(s.y / 100.0, 1e-8, 1.0)
    y = (1.0 - r) ** 2 / (2.0 * r)
    return s.derive(y, "%R -> Kubelka-Munk", y_unit=YUnit.KUBELKA_MUNK)


_TO_CM1 = {
    XUnit.WAVENUMBER: lambda v: v,
    XUnit.WAVELENGTH_UM: lambda v: 1.0e4 / v,
    XUnit.WAVELENGTH_NM: lambda v: 1.0e7 / v,
}
_FROM_CM1 = {
    XUnit.WAVENUMBER: lambda v: v,
    XUnit.WAVELENGTH_UM: lambda v: 1.0e4 / v,
    XUnit.WAVELENGTH_NM: lambda v: 1.0e7 / v,
}


def convert_x(s: Spectrum, to: XUnit) -> Spectrum:
    """Convert the x axis between wavenumber (cm-1), micrometres and nanometres.

    ``XUnit.POINTS`` has no physical scale and cannot be converted to or from.
    """
    if s.x_unit == to:
        return s.copy()
    if s.x_unit == XUnit.POINTS or to == XUnit.POINTS:
        raise ValueError("XUnit.POINTS has no physical scale; cannot convert")

    with np.errstate(divide="ignore"):
        cm1 = _TO_CM1[s.x_unit](s.x)
        new_x = _FROM_CM1[to](cm1)

    return s.derive(s.y.copy(), f"{s.x_unit.value} -> {to.value}", x=new_x, x_unit=to)
