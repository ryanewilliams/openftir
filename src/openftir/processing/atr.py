"""ATR (Attenuated Total Reflectance) correction.

Assumption / simplification: the ATR penetration depth is

    dp(nu) = lambda / (2*pi*n1*sqrt(sin(theta)**2 - (n2/n1)**2))

For a fixed crystal/sample index pair and incidence angle, the trigonometric
term is independent of wavenumber, so it cancels exactly once the correction
is normalised to a reference wavenumber -- leaving the standard
"penetration depth proportional to wavelength" scaling:

    corrected(nu) = raw(nu) * (nu / 1000)

(unchanged at 1000 cm-1, boosted above it, reduced below it, matching the
usual qualitative ATR distortion where low-wavenumber bands are
over-emphasised by the deeper penetration depth). ``n_crystal``,
``n_sample`` and ``angle_deg`` are still used to verify the total-internal-
reflection condition (``sin(angle) > n_sample/n_crystal``); a more complete
model would also include anomalous dispersion near strong bands, which is
not implemented here. ``reflections`` is recorded in metadata but does not
change the per-wavenumber shape of this simplified correction.
"""

from __future__ import annotations

import numpy as np

from openftir.spectrum import Spectrum, YUnit

__all__ = ["atr_correction"]


def atr_correction(
    s: Spectrum,
    n_crystal: float = 2.4,
    n_sample: float = 1.5,
    angle_deg: float = 45.0,
    reflections: int = 1,
) -> Spectrum:
    """Correct an ATR absorbance spectrum for wavenumber-dependent penetration depth."""
    if s.y_unit != YUnit.ABSORBANCE:
        raise ValueError("atr_correction expects an absorbance spectrum; convert first")

    angle = np.deg2rad(angle_deg)
    critical_ratio = n_sample / n_crystal
    if np.sin(angle) <= critical_ratio:
        raise ValueError(
            f"angle_deg={angle_deg} is below the critical angle for total internal "
            f"reflection (n_sample/n_crystal={critical_ratio:.3f}); no valid ATR geometry"
        )

    factor = s.x / 1000.0
    y = s.y * factor
    step = f"ATR correction (n_crystal={n_crystal}, n_sample={n_sample}, angle={angle_deg} deg)"
    return s.derive(
        y,
        step,
        meta={**s.meta, "atr_reflections": reflections, "atr_n_crystal": n_crystal, "atr_n_sample": n_sample},
    )
