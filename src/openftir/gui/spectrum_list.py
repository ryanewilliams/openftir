"""Qt model backing the spectrum list dock.

``SpectrumListModel`` owns the in-memory collection of loaded/collected/
derived spectra shown in the GUI. Each row has a display name (independent of
``Spectrum.title``), a visibility checkbox, and an assigned plot colour.
Processing actions append new rows (non-destructive); nothing in this module
ever mutates a ``Spectrum`` in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from openftir.spectrum import Spectrum

# Colour-blind friendly-ish categorical palette, cycled for new spectra.
_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

SpectrumRole = Qt.UserRole + 1
ColorRole = Qt.UserRole + 2


@dataclass
class SpectrumEntry:
    spectrum: Spectrum
    name: str
    visible: bool = True
    color: QColor = field(default_factory=lambda: QColor(_PALETTE[0]))
    # Manually picked peak positions (cm-1), both in Simple and Advanced mode
    # -- see openftir.gui.plot's pick-peaks mode. Mirrored into
    # ``spectrum.meta["manual_peaks"]`` so they round-trip through .ofjson
    # save/load (see SpectrumListModel.add/set_manual_peaks below).
    # Processing actions that derive a new spectrum never copy this list.
    manual_peaks: list[float] = field(default_factory=list)


class SpectrumListModel(QAbstractListModel):
    """List of :class:`SpectrumEntry` rows, each with a checkbox and colour."""

    visibility_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[SpectrumEntry] = []
        self._next_color = 0

    # -- Qt model interface -------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._entries)

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return (
            Qt.ItemIsEnabled
            | Qt.ItemIsSelectable
            | Qt.ItemIsEditable
            | Qt.ItemIsUserCheckable
        )

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return entry.name
        if role == Qt.CheckStateRole:
            return Qt.Checked if entry.visible else Qt.Unchecked
        if role == Qt.DecorationRole:
            return entry.color
        if role == SpectrumRole:
            return entry.spectrum
        if role == ColorRole:
            return entry.color
        if role == Qt.ToolTipRole:
            return f"{entry.name} ({len(entry.spectrum)} pts, {entry.spectrum.y_unit.value})"
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return False
        entry = self._entries[index.row()]
        if role == Qt.EditRole:
            new_name = str(value).strip()
            if new_name:
                entry.name = new_name
                self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True
        if role == Qt.CheckStateRole:
            entry.visible = value == Qt.Checked or value == Qt.Checked.value
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            self.visibility_changed.emit()
            return True
        if role == ColorRole:
            entry.color = value
            self.dataChanged.emit(index, index, [Qt.DecorationRole, ColorRole])
            self.visibility_changed.emit()
            return True
        return False

    # -- convenience API used by the rest of the GUI -------------------------

    def entries(self) -> list[SpectrumEntry]:
        return list(self._entries)

    def visible_entries(self) -> list[SpectrumEntry]:
        return [e for e in self._entries if e.visible]

    def entry_at(self, row: int) -> SpectrumEntry:
        return self._entries[row]

    def spectrum_at(self, row: int) -> Spectrum:
        return self._entries[row].spectrum

    def add(
        self,
        spectrum: Spectrum,
        name: str | None = None,
        visible: bool = True,
        manual_peaks: list[float] | None = None,
    ) -> int:
        """Append a new entry, returning its row index.

        ``manual_peaks`` is only meant for restoring a spectrum that already
        carries them (e.g. loading a .ofjson file -- see
        MainWindow._open_paths): leave it ``None`` for every other caller
        (a freshly collected spectrum, or one just derived by a processing
        action) so manual peaks are never silently inherited from whatever
        happened to be left in ``spectrum.meta`` -- processing functions
        copy ``meta`` wholesale, so a derived spectrum's dict can still
        carry a stale ``"manual_peaks"`` key from its source.
        """
        final_name = name or spectrum.title or f"Spectrum {len(self._entries) + 1}"
        color = QColor(_PALETTE[self._next_color % len(_PALETTE)])
        self._next_color += 1
        row = len(self._entries)
        if manual_peaks is None:
            spectrum.meta.pop("manual_peaks", None)
            manual_peaks = []
        else:
            manual_peaks = list(manual_peaks)
            spectrum.meta["manual_peaks"] = list(manual_peaks)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(
            SpectrumEntry(spectrum=spectrum, name=final_name, color=color, visible=visible, manual_peaks=manual_peaks)
        )
        self.endInsertRows()
        self.visibility_changed.emit()
        return row

    def set_manual_peaks(self, row: int, positions: list[float]) -> None:
        """Replace the manual peak positions (cm-1) for ``row``, mirroring
        them into ``spectrum.meta["manual_peaks"]`` for persistence."""
        if not (0 <= row < len(self._entries)):
            return
        entry = self._entries[row]
        entry.manual_peaks = sorted(float(p) for p in positions)
        entry.spectrum.meta["manual_peaks"] = list(entry.manual_peaks)
        index = self.index(row)
        self.dataChanged.emit(index, index)

    def replace_spectrum(self, row: int, spectrum: Spectrum) -> None:
        """Swap the Spectrum object held at ``row`` in place (same name/colour)."""
        self._entries[row].spectrum = spectrum
        index = self.index(row)
        self.dataChanged.emit(index, index)
        self.visibility_changed.emit()

    def remove(self, row: int) -> None:
        if not (0 <= row < len(self._entries)):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._entries[row]
        self.endRemoveRows()
        self.visibility_changed.emit()

    def clear(self) -> None:
        if not self._entries:
            return
        self.beginResetModel()
        self._entries.clear()
        self.endResetModel()
        self.visibility_changed.emit()
