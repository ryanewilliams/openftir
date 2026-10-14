"""Background-thread helpers so instrument/IO calls never block the Qt event loop.

Two small ``QThread`` subclasses:

- :class:`CallWorker` runs any single callable (connect, disconnect,
  diagnostics, library search, file IO, ...) and reports back a result or an
  error.
- :class:`CollectWorker` runs ``Instrument.collect_single_beam`` specifically,
  because that call also wants progress callbacks and a ``threading.Event``
  based cancel switch (per ``openftir.instruments.base``).

Always launch workers with :func:`start_worker`, never ``worker.start()``
directly: it keeps a strong reference until the thread has actually finished.
Dropping the last Python reference to a running ``QThread`` makes Qt abort
the whole process ("QThread: Destroyed while thread is still running"), and a
result signal can easily start the next worker before the previous thread's
``run()`` has returned.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

from PySide6.QtCore import QThread, Signal

from openftir.instruments.base import CollectionCancelled, CollectParams, Instrument


log = logging.getLogger(__name__)

_W = TypeVar("_W", bound=QThread)

# Workers that have been started and whose thread has not finished yet.
_running: set[QThread] = set()


def start_worker(worker: _W) -> _W:
    """Start ``worker`` and keep it alive until its thread has finished."""
    _running.add(worker)
    worker.finished.connect(lambda: _running.discard(worker))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker


def wait_for_workers(timeout_ms: int = 5000) -> None:
    """Block until all running workers finish (call on application exit)."""
    for worker in list(_running):
        worker.wait(timeout_ms)


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
            log.exception("background call failed")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class CollectWorker(QThread):
    """Runs one ``collect_single_beam`` acquisition with progress + cancel support."""

    progress = Signal(int, int)
    succeeded = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, instrument: Instrument, params: CollectParams, parent=None, lock: threading.Lock | None = None):
        super().__init__(parent)
        self.instrument = instrument
        self.params = params
        self.cancel_event = threading.Event()
        self._lock = lock

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        if self._lock is not None:
            with self._lock:
                self._collect()
        else:
            self._collect()

    def _collect(self) -> None:
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
            log.exception("collection failed")
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(spectrum)
