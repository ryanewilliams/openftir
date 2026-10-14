"""Spectral processing core: conversions, baselines, smoothing, arithmetic,
peak finding, ATR correction, interferogram FFT, and library search.

All public functions take and return :class:`openftir.spectrum.Spectrum`
objects (never mutating their inputs) unless documented otherwise -- a few
analysis helpers (``auto_subtract_factor``, ``peak_area``,
``nominal_resolution_points``, ``apodize``) inherently produce a scalar or
raw array rather than a spectrum, and ``interferogram_to_single_beam`` /
``single_beam_to_interferogram`` work at the raw-array <-> Spectrum boundary
to match the instrument driver interface.
"""

from .arithmetic import (
    add,
    auto_subtract_factor,
    average,
    crop,
    interpolate_to,
    offset,
    scale,
    subtract,
)
from .atr import atr_correction
from .baseline import baseline_als, baseline_polynomial, baseline_rubberband
from .convert import convert_x, kubelka_munk, ratio, to_absorbance, to_transmittance
from .fft import (
    apodize,
    asymmetric_mertz_single_beam,
    interferogram_to_single_beam,
    nominal_resolution_points,
    single_beam_to_interferogram,
)
from .normalize import normalize
from .peaks import Peak, auto_label_peaks, find_peaks, peak_area
from .search import Match, SpectralLibrary, search
from .smooth import derivative, moving_average, savgol

__all__ = [
    # convert
    "ratio",
    "to_absorbance",
    "to_transmittance",
    "kubelka_munk",
    "convert_x",
    # baseline
    "baseline_polynomial",
    "baseline_als",
    "baseline_rubberband",
    # smooth
    "savgol",
    "derivative",
    "moving_average",
    # normalize
    "normalize",
    # arithmetic
    "crop",
    "interpolate_to",
    "subtract",
    "add",
    "scale",
    "offset",
    "average",
    "auto_subtract_factor",
    # peaks
    "Peak",
    "find_peaks",
    "peak_area",
    "auto_label_peaks",
    # atr
    "atr_correction",
    # fft
    "apodize",
    "asymmetric_mertz_single_beam",
    "interferogram_to_single_beam",
    "single_beam_to_interferogram",
    "nominal_resolution_points",
    # search
    "SpectralLibrary",
    "Match",
    "search",
]
