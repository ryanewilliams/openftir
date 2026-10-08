"""Background-thread helpers so instrument/IO calls never block the Qt event loop.

Two small ``QThread`` subclasses:

- :class:`CallWorker` runs any single callable (connect, disconnect,
  diagnostics, library search, file IO, ...) and reports back a result or an
  error.
- :class:`CollectWorker` runs ``Instrument.collect_single_beam`` specifically,
  because that call also wants progress callbacks and a ``threading.Event``
  based cancel switch (per ``openftir.instruments.base``).
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from openftir.instruments.base import CollectionCancelled, CollectParams, Instrument


class CallWorker(QThread):
    """Runs ``fn(*args, **kwargs)`` on a worker thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent=None, **kwargs: Any):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - reported to the UI, not swallowed
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class CollectWorker(QThread):
    """Runs one ``collect_single_beam`` acquisition with progress + cancel support."""

    progress = Signal(int, int)
    succeeded = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, instrument: Instrument, params: CollectParams, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self.params = params
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            spectrum = self.instrument.collect_single_beam(
                self.params,
                progress=lambda done, total: self.progress.emit(done, total),
                cancel=self.cancel_event,
            )
        except CollectionCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(spectrum)
