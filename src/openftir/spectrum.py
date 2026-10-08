"""Core data model shared by every part of OpenFTIR.

A ``Spectrum`` is an immutable-ish container: processing functions return new
``Spectrum`` objects instead of mutating their input.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np


class XUnit(str, Enum):
    WAVENUMBER = "cm-1"        # most FTIR data
    WAVELENGTH_UM = "um"
    WAVELENGTH_NM = "nm"
    POINTS = "points"          # raw interferogram / unknown axis


class YUnit(str, Enum):
    ABSORBANCE = "absorbance"
    TRANSMITTANCE = "transmittance"            # percent, 0-100
    REFLECTANCE = "reflectance"                # percent, 0-100
    KUBELKA_MUNK = "kubelka-munk"
    LOG_INV_REFLECTANCE = "log(1/R)"
    SINGLE_BEAM = "single beam"                # arbitrary intensity
    INTERFEROGRAM = "interferogram"
    ARBITRARY = "arbitrary"


@dataclass
class Spectrum:
    """One spectrum: an x axis, matching y values, and metadata.

    ``x`` may be ascending or descending; use ``sorted_ascending()`` when an
    algorithm needs a monotonic increasing axis.
    """

    x: np.ndarray
    y: np.ndarray
    x_unit: XUnit = XUnit.WAVENUMBER
    y_unit: YUnit = YUnit.ABSORBANCE
    title: str = ""
    # Free-form metadata: resolution, scans, gain, source file, collection
    # parameters, processing history, etc. Values should be JSON-serialisable.
    meta: dict[str, Any] = field(default_factory=dict)
    acquired: datetime | None = None
    # Ordered list of human-readable processing steps applied so far.
    history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        if self.x.ndim != 1 or self.y.ndim != 1:
            raise ValueError("x and y must be 1-D arrays")
        if self.x.shape != self.y.shape:
            raise ValueError(f"x and y length mismatch: {self.x.shape} vs {self.y.shape}")

    def __len__(self) -> int:
        return self.x.size

    def copy(self, **changes: Any) -> "Spectrum":
        """Deep-ish copy with optional field overrides."""
        base = dict(
            x=self.x.copy(),
            y=self.y.copy(),
            meta=dict(self.meta),
            history=list(self.history),
        )
        base.update(changes)
        return dataclasses.replace(self, **base)

    def derive(self, y: np.ndarray, step: str, x: np.ndarray | None = None, **changes: Any) -> "Spectrum":
        """Return a new spectrum with new values and ``step`` appended to history."""
        return self.copy(
            x=self.x.copy() if x is None else np.asarray(x, dtype=float),
            y=np.asarray(y, dtype=float),
            history=[*self.history, step],
            **changes,
        )

    def sorted_ascending(self) -> "Spectrum":
        order = np.argsort(self.x)
        return self.copy(x=self.x[order], y=self.y[order])
