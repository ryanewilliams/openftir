"""Sessions: everything open in the app, saved to one ``.ofjson`` file.

A session file is an ordinary ``.ofjson`` spectra file (so any OpenFTIR
version can open the spectra) plus a ``"session"`` block with what the app
needs to put things back exactly: each spectrum's name, colour and
visibility, which one is the background (and when it was collected), and
which one was selected. Manual peak picks already travel in each
spectrum's ``meta``.

The GUI also autosaves a session (see ``MainWindow``), so closing the app,
a crash or a restart doesn't lose work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from openftir import __version__
from openftir.io import _ofjson
from openftir.spectrum import Spectrum

SESSION_VERSION = 1


@dataclass
class SessionEntry:
    spectrum: Spectrum
    name: str
    color: str = ""  # "#rrggbb"; empty = let the app pick
    visible: bool = True
    manual_peaks: list[float] = field(default_factory=list)


@dataclass
class Session:
    entries: list[SessionEntry]
    background: int | None = None  # index into entries
    background_time: datetime | None = None
    selected: int | None = None
    saved_at: datetime | None = None


def save_session(session: Session, path: str | os.PathLike) -> None:
    spectra = []
    for e in session.entries:
        e.spectrum.meta["manual_peaks"] = list(e.manual_peaks)
        spectra.append(e.spectrum)
    block = {
        "version": SESSION_VERSION,
        "app_version": __version__,
        "saved_at": (session.saved_at or datetime.now()).isoformat(timespec="seconds"),
        "entries": [{"name": e.name, "color": e.color, "visible": e.visible} for e in session.entries],
        "background": session.background,
        "background_time": session.background_time.isoformat() if session.background_time else None,
        "selected": session.selected,
    }
    _ofjson.write_ofjson(spectra, path, session=block)


def load_session(path: str | os.PathLike) -> Session | None:
    """The session in ``path``, or None if it's a plain spectra file."""
    spectra, block = _ofjson.read_document(path)
    if block is None:
        return None
    infos = block.get("entries") or []
    entries = []
    for i, spectrum in enumerate(spectra):
        info = infos[i] if i < len(infos) and isinstance(infos[i], dict) else {}
        entries.append(SessionEntry(
            spectrum=spectrum,
            name=str(info.get("name") or spectrum.title or f"Spectrum {i + 1}"),
            color=str(info.get("color") or ""),
            visible=bool(info.get("visible", True)),
            manual_peaks=[float(p) for p in spectrum.meta.get("manual_peaks", []) or []],
        ))

    def index(key):
        value = block.get(key)
        return value if isinstance(value, int) and 0 <= value < len(entries) else None

    def when(key):
        value = block.get(key)
        try:
            return datetime.fromisoformat(value) if value else None
        except (TypeError, ValueError):
            return None

    return Session(
        entries=entries,
        background=index("background"),
        background_time=when("background_time"),
        selected=index("selected"),
        saved_at=when("saved_at"),
    )
