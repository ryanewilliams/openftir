import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import threading
import time

from openftir.gui import workers
from openftir.gui.workers import CallWorker, start_worker


def test_started_workers_survive_dropped_references(qtbot):
    # Regression: replacing the only reference to a running QThread let Python
    # collect it mid-run, which makes Qt abort the process (seen on Windows CI).
    release = threading.Event()
    results = []

    for i in range(5):
        worker = CallWorker(lambda i=i: (release.wait(2), i)[1])
        worker.succeeded.connect(results.append)
        start_worker(worker)
    del worker
    gc.collect()

    release.set()
    qtbot.waitUntil(lambda: len(results) == 5, timeout=5000)
    qtbot.waitUntil(lambda: not workers._running, timeout=5000)
    assert sorted(results) == [0, 1, 2, 3, 4]


def test_wait_for_workers_blocks_until_done(qtbot):
    worker = start_worker(CallWorker(time.sleep, 0.2))
    workers.wait_for_workers()
    assert worker.isFinished()
