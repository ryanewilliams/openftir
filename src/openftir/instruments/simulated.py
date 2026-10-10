"""Simulated FTIR instrument driver.

Produces plausible single-beam spectra and interferograms for a Nicolet
Summit-like bench-top FTIR without any hardware attached, so the GUI/CLI and
processing pipeline can be developed and tested end to end.

Physics model
-------------
For each requested collection we build an *ideal* (noiseless) single-beam
spectrum on a fine wavenumber grid:

    ideal(nu) = source(nu) * optics_and_detector(nu) * atmosphere(nu) * sample(nu)

- ``source`` is a glowbar (silicon carbide) blackbody emission curve.
- ``optics_and_detector`` folds in a KBr beamsplitter/window low-wavenumber
  cut-off and a DTGS detector's roughly bell-shaped responsivity, giving the
  familiar single-beam "hump" shape of a real instrument.
- ``atmosphere`` is CO2 (asymmetric stretch ~2349 cm-1, bend ~667 cm-1) and
  water vapour (rotational-vibrational combs around 1300-2000 cm-1 and
  3500-3950 cm-1) absorbance in the (imperfectly purged) beam path. Their
  strength drifts slightly, collection to collection, via a small random
  walk -- so ratioing a sample scan against an earlier background scan shows
  the realistic atmospheric residuals seen on real instruments.
- ``sample`` is ``10 ** (-absorbance)`` for whatever is currently on the
  (virtual) sample stage (see :meth:`SimulatedInstrument.set_sample`).

That ideal single-beam spectrum is converted to an ideal interferogram with
:func:`openftir.processing.fft.single_beam_to_interferogram` (point count
from :func:`nominal_resolution_points`). Each scan adds independent Gaussian
noise to that same ideal interferogram and the noisy copies are co-added
(averaged), so noise falls off roughly as ``1/sqrt(scans)`` -- exactly as
real co-addition does. The averaged interferogram is then converted back to
a single-beam spectrum with
:func:`openftir.processing.fft.interferogram_to_single_beam`, using the
collection's apodization/zero-fill/phase-correction settings, so the whole
real FFT pipeline is exercised rather than faking a spectrum directly.
"""

from __future__ import annotations

import threading
import time
from typing import Sequence

import numpy as np

from openftir.instruments.base import (
    CollectionCancelled,
    CollectParams,
    DiagnosticsReport,
    Instrument,
    InstrumentError,
    InstrumentInfo,
    ProgressCallback,
)
from openftir.processing.fft import (
    interferogram_to_single_beam,
    nominal_resolution_points,
    single_beam_to_interferogram,
)
from openftir.spectrum import Spectrum, XUnit, YUnit

__all__ = ["SimulatedInstrument"]

_LASER_WAVENUMBER = 15798.0  # HeNe, cm-1

#: Built-in synthetic samples. Band list: (center cm-1, peak absorbance, FWHM cm-1).
_SAMPLE_BANDS: dict[str, tuple[tuple[float, float, float], ...]] = {
    "polystyrene": (
        (3082.0, 0.050, 8.0),
        (3060.0, 0.045, 8.0),
        (3026.0, 0.080, 8.0),
        (2924.0, 0.450, 14.0),
        (2850.0, 0.250, 12.0),
        (1601.0, 0.280, 10.0),
        (1583.0, 0.080, 8.0),
        (1493.0, 0.550, 10.0),
        (1452.0, 0.350, 10.0),
        (1028.0, 0.250, 10.0),
        (906.0, 0.120, 8.0),
        (757.0, 0.550, 8.0),
        (698.0, 0.650, 10.0),
    ),
    # (R)-(+)-limonene, neat liquid on a diamond ATR: approximate band
    # positions/relative intensities from its well-known IR spectrum.
    "limonene": (
        (3083.0, 0.060, 10.0),   # =CH2 asym stretch (vinylidene)
        (3008.0, 0.050, 12.0),   # =C-H stretch (ring)
        (2965.0, 0.300, 16.0),   # CH3 asym stretch
        (2918.0, 0.650, 18.0),   # CH2 asym stretch (strongest)
        (2856.0, 0.350, 14.0),   # CH2 sym stretch
        (2834.0, 0.200, 10.0),
        (2725.0, 0.030, 12.0),
        (1678.0, 0.050, 10.0),   # C=C stretch, trisubstituted ring
        (1644.0, 0.180, 10.0),   # C=C stretch, vinylidene
        (1452.0, 0.250, 12.0),   # CH2 scissor
        (1436.0, 0.300, 10.0),
        (1376.0, 0.180, 9.0),    # CH3 umbrella
        (1311.0, 0.050, 10.0),
        (1150.0, 0.060, 12.0),
        (1050.0, 0.050, 10.0),
        (1015.0, 0.050, 10.0),
        (948.0, 0.060, 10.0),
        (915.0, 0.080, 10.0),
        (886.0, 0.600, 9.0),     # =CH2 out-of-plane wag (diagnostic)
        (798.0, 0.220, 10.0),    # trisubstituted C=C-H wag
        (756.0, 0.060, 10.0),
    ),
    # A "wet" / vapour-cell sample: just an extra-strong dose of the same
    # water-vapour comb used for the atmospheric background, so the
    # collected spectrum is dominated by sharp rotational-vibrational lines.
    "water-vapour": (),
    # Empty beam -- no sample absorbance at all (background / open beam).
    "none": (),
}

# Fixed (deterministic) atmospheric line positions -- real water vapour and
# CO2 rotational-vibrational line catalogues are enormous; this is a sparse
# stand-in with plausible band shapes, not a line-by-line HITRAN model.
_CO2_BANDS = ((2349.0, 0.9, 6.0), (2284.0, 0.08, 10.0), (667.0, 0.35, 14.0))

_WATER_LINE_CENTERS = np.concatenate(
    [
        np.linspace(1320.0, 1990.0, 18),
        np.linspace(3520.0, 3940.0, 14),
    ]
)


def _water_line_heights() -> np.ndarray:
    # Fixed, deterministic pseudo-random heights (independent of the
    # instrument's own seed) so the line catalogue itself never changes.
    rng = np.random.default_rng(20240115)
    heights = rng.uniform(0.01, 0.12, size=_WATER_LINE_CENTERS.size)
    return heights


_WATER_LINE_HEIGHTS = _water_line_heights()
_WATER_LINE_WIDTHS = np.where(_WATER_LINE_CENTERS < 2500.0, 3.0, 4.0)


def _lorentzian(x: np.ndarray, center: float, height: float, fwhm: float) -> np.ndarray:
    return height / (1.0 + ((x - center) / (fwhm / 2.0)) ** 2)


def _band_sum(x: np.ndarray, bands: Sequence[tuple[float, float, float]], scale: float = 1.0) -> np.ndarray:
    y = np.zeros_like(x)
    for center, height, fwhm in bands:
        y += _lorentzian(x, center, height * scale, fwhm)
    return y


def _water_vapour_absorbance(x: np.ndarray, scale: float = 1.0) -> np.ndarray:
    y = np.zeros_like(x)
    for center, height, fwhm in zip(_WATER_LINE_CENTERS, _WATER_LINE_HEIGHTS, _WATER_LINE_WIDTHS):
        y += _lorentzian(x, center, height * scale, fwhm)
    return y


def _source_envelope(wn: np.ndarray) -> np.ndarray:
    """Glowbar blackbody x KBr cut-off x DTGS detector response, normalised to 1."""
    h, c, k = 6.62607015e-34, 2.99792458e10, 1.380649e-23  # SI, c in cm/s
    temperature = 1250.0  # glowbar source, kelvin
    nu = np.clip(wn, 1.0, None)
    exponent = np.clip(h * c * nu / (k * temperature), 1e-6, 700.0)
    planck = nu**3 / np.expm1(exponent)
    planck = planck / np.max(planck)

    kbr_cutoff = 1.0 / (1.0 + np.exp(-(wn - 420.0) / 18.0))
    detector = np.exp(-(((wn - 1250.0) / 2600.0) ** 2))
    return planck * kbr_cutoff * detector


class SimulatedInstrument(Instrument):
    """A synthetic, no-hardware FTIR driver for development, demos and tests."""

    driver_id = "simulated"
    display_name = "Simulated FTIR (Summit-like)"

    _SAMPLES = ("polystyrene", "limonene", "water-vapour", "none")

    def __init__(
        self,
        sample: str = "polystyrene",
        noise: float = 0.0008,
        seed: int | None = None,
        scan_time_s: float = 0.0,
        fail_connect: bool = False,
    ) -> None:
        self.set_sample(sample)
        self.noise = float(noise)
        self.seed = seed
        self.scan_time_s = float(scan_time_s)
        self.fail_connect = bool(fail_connect)

        self._rng = np.random.default_rng(seed)
        self._connected = False
        self._co2_level = 1.0
        self._humidity_level = 1.0
        self._info: InstrumentInfo | None = None

    # -- Instrument interface ----------------------------------------------

    def connect(self) -> InstrumentInfo:
        if self.fail_connect:
            raise InstrumentError("simulated instrument: connection failure (fail_connect=True)")
        self._connected = True
        self._info = InstrumentInfo(
            vendor="OpenFTIR Project",
            model="Simulated FTIR (Summit-like)",
            serial="SIM-0001",
            firmware="sim-1.0",
            laser_wavenumber=_LASER_WAVENUMBER,
            resolutions=(0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0),
            extra={"source": "glowbar", "detector": "DTGS", "beamsplitter": "KBr"},
        )
        return self._info

    def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def set_sample(self, name: str) -> None:
        """Put ``name`` on the (virtual) sample stage, e.g. "simulate putting the
        sample on the ATR"."""
        if name not in self._SAMPLES:
            raise ValueError(f"unknown simulated sample {name!r}; choices: {self._SAMPLES}")
        self.sample = name

    def diagnostics(self) -> DiagnosticsReport:
        if not self.connected:
            return DiagnosticsReport(ok=False, items={"connection": "disconnected"})
        humidity_pct = max(2.0, min(20.0, 6.0 + (self._humidity_level - 1.0) * 10.0 + self._rng.normal(0, 0.3)))
        items = {
            "source": "glowbar: OK, ~1250 K",
            "laser": f"HeNe: locked, {_LASER_WAVENUMBER:.1f} cm-1",
            "detector": "DTGS: OK, responsivity nominal",
            "desiccant": f"desiccant: {humidity_pct:.1f}% RH (dry)",
            "interferometer": "Michelson: aligned",
        }
        return DiagnosticsReport(ok=True, items=items)

    def collect_single_beam(
        self,
        params: CollectParams,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Spectrum:
        n_points, igram, meta = self._collect_coadded_interferogram(params, progress, cancel)
        sb = interferogram_to_single_beam(
            igram,
            laser_wavenumber=_LASER_WAVENUMBER,
            apodization=params.apodization,
            zero_fill=params.zero_fill,
            phase_correction=params.phase_correction,
            x_min=params.x_min,
            x_max=params.x_max,
        )
        return sb.copy(
            title=f"Simulated single beam ({self.sample})",
            meta={**sb.meta, **meta},
        )

    def collect_interferogram(self, params: CollectParams) -> Spectrum:
        _n_points, igram, meta = self._collect_coadded_interferogram(params, progress=None, cancel=None)
        return Spectrum(
            x=np.arange(igram.size, dtype=float),
            y=igram,
            x_unit=XUnit.POINTS,
            y_unit=YUnit.INTERFEROGRAM,
            title=f"Simulated interferogram ({self.sample})",
            meta=meta,
            history=["simulated co-added interferogram"],
        )

    # -- internals -----------------------------------------------------------

    def _ideal_single_beam(self, wn: np.ndarray, sample: str | None = None) -> np.ndarray:
        """``sample`` overrides ``self.sample`` for one collection (a
        background is always an empty beam, whatever sample is selected)."""
        sample = self.sample if sample is None else sample
        envelope = _source_envelope(wn)

        atmosphere_absorbance = _band_sum(wn, _CO2_BANDS, scale=self._co2_level)
        atmosphere_absorbance += _water_vapour_absorbance(wn, scale=self._humidity_level)
        atmosphere_transmission = np.power(10.0, -atmosphere_absorbance)

        if _SAMPLE_BANDS.get(sample):
            sample_absorbance = _band_sum(wn, _SAMPLE_BANDS[sample])
        elif sample == "water-vapour":
            sample_absorbance = _water_vapour_absorbance(wn, scale=4.0)
        else:  # "none" -- empty beam
            sample_absorbance = np.zeros_like(wn)
        sample_transmission = np.power(10.0, -sample_absorbance)

        return envelope * atmosphere_transmission * sample_transmission

    def _collect_coadded_interferogram(
        self,
        params: CollectParams,
        progress: ProgressCallback | None,
        cancel: threading.Event | None,
    ) -> tuple[int, np.ndarray, dict]:
        if not self.connected:
            raise InstrumentError("simulated instrument is not connected")
        if params.scans < 1:
            raise ValueError("params.scans must be >= 1")

        # Atmosphere drifts a little between collections (purge imperfections,
        # HVAC, door openings...), so a sample ratioed against an earlier
        # background shows realistic atmospheric residuals.
        self._co2_level = float(np.clip(self._co2_level + self._rng.normal(0, 0.015), 0.7, 1.4))
        self._humidity_level = float(np.clip(self._humidity_level + self._rng.normal(0, 0.05), 0.3, 1.8))

        n_points = 2 * nominal_resolution_points(params.resolution, _LASER_WAVENUMBER)
        wn_grid = np.linspace(50.0, min(0.99 * _LASER_WAVENUMBER / 2.0, 7800.0), 4096)
        ideal_sb = Spectrum(
            x=wn_grid,
            # A background is an empty beam: on a real instrument you don't
            # leave the sample on the crystal while collecting it.
            y=self._ideal_single_beam(wn_grid, "none" if params.extra.get("purpose") == "background" else None),
            x_unit=XUnit.WAVENUMBER,
            y_unit=YUnit.SINGLE_BEAM,
        )
        ideal_igram = single_beam_to_interferogram(ideal_sb, n_points, laser_wavenumber=_LASER_WAVENUMBER).y
        noise_scale = self.noise * np.max(np.abs(ideal_igram))

        accumulated = np.zeros(n_points)
        for i in range(params.scans):
            if cancel is not None and cancel.is_set():
                raise CollectionCancelled("collection cancelled")

            noisy = ideal_igram + self._rng.normal(0.0, noise_scale, size=n_points)
            accumulated += noisy

            if self.scan_time_s > 0:
                time.sleep(self.scan_time_s)
            if progress is not None:
                progress(i + 1, params.scans)

        averaged = accumulated / params.scans
        meta = {
            "driver": self.driver_id,
            "sample": self.sample,
            "scans": params.scans,
            "resolution": params.resolution,
            "apodization": params.apodization,
            "zero_fill": params.zero_fill,
            "phase_correction": params.phase_correction,
            "gain": params.gain,
            "noise": self.noise,
        }
        return n_points, averaged, meta
