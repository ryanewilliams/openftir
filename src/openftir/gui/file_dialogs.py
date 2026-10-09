"""Non-native Qt file dialogs, used everywhere the GUI opens/saves a file.

On an unsigned, frozen macOS build the *native* NSSavePanel/NSOpenPanel runs
out-of-process and never receives keyboard focus, so the user cannot type a
filename into it at all. Every file dialog in the GUI goes through this
module instead, which always constructs a plain ``QFileDialog`` with
``DontUseNativeDialog`` set, so typing (and filtering) works regardless of
platform or code-signing status. It also keeps a few small conveniences
consistent across every dialog: a sensible pre-filled filename, the
last-used directory, and returning keyboard focus to the main window once
the (possibly modal, possibly OS-focus-stealing) dialog closes.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QWidget

_ORG = "OpenFTIR"
_APP = "OpenFTIR"
_LAST_DIR_KEY = "fileDialogs/lastDirectory"

# Characters illegal (or awkward) in a filename on Windows/macOS/Linux.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXTENSION_RE = re.compile(r"\*(\.[A-Za-z0-9]+)")


def sanitize_filename(name: str) -> str:
    """Strip characters that are illegal in a filename on any common OS."""
    cleaned = _INVALID_CHARS.sub("_", name or "").strip().strip(".")
    return cleaned or "Untitled"


def filter_extension(filter_text: str) -> str:
    """The first ``*.ext`` found in a filter entry, e.g. ``.jdx`` from
    ``"JCAMP-DX spectrum (*.jdx *.dx)"``. Empty string if there is none."""
    match = _EXTENSION_RE.search(filter_text or "")
    return match.group(1).lower() if match else ""


def build_filter_string(filters: dict, include_all_supported: bool = True) -> str:
    """Build a ``QFileDialog`` filter string from an ``{ext: description}`` map."""
    if not filters:
        return "All Files (*)"
    parts = [f"{desc} (*{ext})" for ext, desc in filters.items()]
    if include_all_supported:
        all_exts = " ".join(f"*{ext}" for ext in filters)
        parts = [f"All Supported ({all_exts})"] + parts
    return ";;".join(parts) + ";;All Files (*)"


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def default_directory() -> str:
    """Last-used directory, else ``~/Documents`` if it exists, else home."""
    last = _settings().value(_LAST_DIR_KEY, "", type=str)
    if last and Path(last).is_dir():
        return last
    documents = Path.home() / "Documents"
    if documents.is_dir():
        return str(documents)
    return str(Path.home())


def _remember_directory(path: str) -> None:
    if not path:
        return
    try:
        directory = str(Path(path).expanduser().resolve().parent)
    except OSError:
        return
    _settings().setValue(_LAST_DIR_KEY, directory)


def _refocus(parent: QWidget | None) -> None:
    """Native dialogs (and on macOS, even non-native ones) can leave the
    main window without keyboard focus once they close; take it back."""
    window = parent.window() if parent is not None else None
    if window is not None:
        window.activateWindow()
        window.raise_()


def finalize_save_path(path: str, selected_filter: str) -> str:
    """Append the selected filter's extension if the user typed a bare name."""
    if not path or Path(path).suffix:
        return path
    ext = filter_extension(selected_filter)
    return path + ext if ext else path


# -- dialog builders (construct only -- never exec, so tests can inspect them) ----


def build_save_dialog(parent: QWidget | None, title: str, default_name: str, name_filters: str) -> QFileDialog:
    """A non-native Save dialog, pre-filled with ``default_name`` in the
    last-used directory. The filename's extension tracks whichever filter
    the user picks (``filterSelected``), the way native dialogs behave."""
    start_path = str(Path(default_directory()) / default_name)
    dialog = QFileDialog(parent, title, start_path, name_filters)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)

    def _sync_extension(selected_filter: str) -> None:
        new_ext = filter_extension(selected_filter)
        if not new_ext:
            return
        current = dialog.selectedFiles()
        base = Path(current[0]).name if current else default_name
        suffix = Path(base).suffix
        stem = base[: -len(suffix)] if suffix else base
        dialog.selectFile(stem + new_ext)

    dialog.filterSelected.connect(_sync_extension)
    return dialog


def build_open_dialog(parent: QWidget | None, title: str, name_filters: str, multiple: bool = True) -> QFileDialog:
    """A non-native Open dialog rooted at the last-used directory."""
    dialog = QFileDialog(parent, title, default_directory(), name_filters)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles if multiple else QFileDialog.FileMode.ExistingFile)
    return dialog


def build_directory_dialog(parent: QWidget | None, title: str) -> QFileDialog:
    """A non-native "choose a folder" dialog rooted at the last-used directory."""
    dialog = QFileDialog(parent, title, default_directory())
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    return dialog


# -- convenience wrappers: build, exec, refocus, remember -------------------------


def get_save_path(parent: QWidget | None, title: str, default_name: str, name_filters: str) -> str:
    """Show the Save dialog; '' if the user cancelled."""
    dialog = build_save_dialog(parent, title, default_name, name_filters)
    path = ""
    try:
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected:
                path = finalize_save_path(selected[0], dialog.selectedNameFilter())
    finally:
        _refocus(parent)
    if path:
        _remember_directory(path)
    return path


def get_open_paths(parent: QWidget | None, title: str, name_filters: str, multiple: bool = True) -> list[str]:
    """Show the Open dialog; ``[]`` if the user cancelled."""
    dialog = build_open_dialog(parent, title, name_filters, multiple=multiple)
    paths: list[str] = []
    try:
        if dialog.exec():
            paths = list(dialog.selectedFiles())
    finally:
        _refocus(parent)
    if paths:
        _remember_directory(paths[0])
    return paths


def get_existing_directory(parent: QWidget | None, title: str) -> str:
    """Show the "choose a folder" dialog; '' if the user cancelled."""
    dialog = build_directory_dialog(parent, title)
    path = ""
    try:
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected:
                path = selected[0]
    finally:
        _refocus(parent)
    if path:
        _remember_directory(path)
    return path
