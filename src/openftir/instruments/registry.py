"""Driver registry.

Built-in drivers are listed in ``_BUILTIN``. Third-party packages can add
drivers through the ``openftir.drivers`` entry-point group, e.g. in their
pyproject.toml::

    [project.entry-points."openftir.drivers"]
    my_ftir = "my_package.driver:MyFtirDriver"
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points

from openftir.instruments.base import Instrument

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "openftir.drivers"

# driver_id -> "module:ClassName"; imported lazily so a broken or
# hardware-specific driver never prevents the app from starting.
_BUILTIN: dict[str, str] = {
    "simulated": "openftir.instruments.simulated:SimulatedInstrument",
    "summit": "openftir.instruments.summit:SummitInstrument",
}


def _load(target: str) -> type[Instrument]:
    module_name, _, class_name = target.partition(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(cls, type) and issubclass(cls, Instrument)):
        raise TypeError(f"{target} is not an Instrument subclass")
    return cls


def available_drivers() -> dict[str, type[Instrument]]:
    """Return every driver that imports cleanly, keyed by driver id."""
    drivers: dict[str, type[Instrument]] = {}
    for driver_id, target in _BUILTIN.items():
        try:
            drivers[driver_id] = _load(target)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            log.warning("built-in driver %s unavailable: %s", driver_id, exc)
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            drivers[ep.name] = _load(ep.value)
        except Exception as exc:  # noqa: BLE001
            log.warning("plugin driver %s unavailable: %s", ep.name, exc)
    return drivers


def create(driver_id: str, **kwargs) -> Instrument:
    """Instantiate a driver by id. ``kwargs`` go to the driver constructor."""
    drivers = available_drivers()
    try:
        cls = drivers[driver_id]
    except KeyError:
        raise KeyError(f"unknown driver {driver_id!r}; available: {sorted(drivers)}") from None
    return cls(**kwargs)
