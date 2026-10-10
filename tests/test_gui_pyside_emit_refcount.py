"""Guard against PySide6 builds whose Signal.emit() leaks a decref on True.

PySide6 6.12.0 has this bug. On Python < 3.12 (where True is not immortal)
every emit lowers True's refcount, and after enough emits the interpreter
aborts at exit with "free(): invalid pointer". pyproject.toml excludes that
version on those Pythons; this test catches any other affected version.
"""

import gc
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal


class _Emitter(QObject):
    fired = Signal(int)


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="True is immortal on Python 3.12+")
def test_signal_emit_does_not_leak_true_refcount(qapp):
    emitter = _Emitter()
    emitter.fired.connect(lambda _v: None)
    gc.collect()
    before = sys.getrefcount(True)
    for _ in range(200):
        emitter.fired.emit(1)
    gc.collect()
    # Allow small noise from unrelated objects; the bug costs exactly 200.
    assert sys.getrefcount(True) - before > -20
