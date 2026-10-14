"""The app's log file, so problems seen in the lab can be looked at later
(e.g. the Varian's alignment, or a driver error's full traceback)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or str(Path.home() / ".openftir")
    return Path(base) / "openftir.log"


def start_logging(path: Path | None = None) -> Path | None:
    """Log OpenFTIR's messages (INFO and up) to a small rotating file."""
    path = path or log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger = logging.getLogger("openftir")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    from openftir import __version__

    logger.info("OpenFTIR %s started", __version__)
    return path
