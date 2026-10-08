"""Instrument driver interface.

Every spectrometer driver (simulated, Nicolet Summit, ...) implements
``Instrument``. The GUI and CLI only talk to this interface, so new hardware
support never requires touching the UI.

Drivers are synchronous and may block; the GUI runs them in a worker thread.
Long operations must call ``progress`` (if given) and honour ``cancel``.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from typing import Callable

from openftir.spectrum import Spectrum


class InstrumentError(RuntimeError):
    pass


class CollectionCancelled(InstrumentError):
    pass


@dataclass
class CollectParams:
    scans: int = 16
    resolution: float = 4.0            # cm-1
    x_min: float = 600.0               # cm-1, output range (Summit X default)
    x_max: float = 4000.0
    apodization: str = "happ-genzel"
    zero_fill: int = 2                 # zero-filling factor (1 = none)
    phase_correction: str = "mertz"
    gain: str = "auto"
    extra: dict = field(default_factory=dict)   # driver-specific options


@dataclass
class InstrumentInfo:
    vendor: str
    model: str
    serial: str = ""
    firmware: str = ""
    # Laser wavenumber in cm-1 (HeNe = 15798.0), needed for the FFT x axis.
    laser_wavenumber: float = 15798.0
    resolutions: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
    extra: dict = field(default_factory=dict)


@dataclass
class DiagnosticsReport:
    ok: bool
    items: dict[str, str] = field(default_factory=dict)   # name -> status text


ProgressCallback = Callable[[int, int], None]   # (scans_done, scans_total)


class Instrument(abc.ABC):
    """Base class for all spectrometer drivers."""

    #: short unique id used in the driver registry, e.g. "simulated"
    driver_id: str = ""
    #: human readable name shown in the UI
    display_name: str = ""

    @abc.abstractmethod
    def connect(self) -> InstrumentInfo: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abc.abstractmethod
    def connected(self) -> bool: ...

    @abc.abstractmethod
    def collect_single_beam(
        self,
        params: CollectParams,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Spectrum:
        """Co-add ``params.scans`` scans and return a single-beam spectrum
        (``YUnit.SINGLE_BEAM``, wavenumber axis restricted to x_min..x_max)."""

    def collect_interferogram(self, params: CollectParams) -> Spectrum:
        """Optional: raw co-added interferogram (``YUnit.INTERFEROGRAM``)."""
        raise NotImplementedError

    def diagnostics(self) -> DiagnosticsReport:
        """Optional: source/laser/detector health checks."""
        return DiagnosticsReport(ok=self.connected, items={"connection": "ok" if self.connected else "disconnected"})

    def __enter__(self) -> "Instrument":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()
