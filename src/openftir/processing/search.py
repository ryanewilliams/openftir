"""Local spectral library and library search/matching."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from openftir.spectrum import Spectrum, XUnit, YUnit

from ._utils import interp_values
from .convert import to_absorbance

__all__ = ["SpectralLibrary", "Match", "search"]

_DEFAULT_GRID_POINTS = 1024
_DEFAULT_X_RANGE = (400.0, 4000.0)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return slug or "entry"


class SpectralLibrary:
    """A directory-backed library of reference spectra.

    Layout: ``<path>/index.json`` plus one ``<path>/<slug>.npz`` per entry.
    """

    def __init__(self, path: Path, index: list[dict[str, Any]]):
        self.path = Path(path)
        self._index = index  # list of {"name", "file", "x_unit", "y_unit", "meta"}
        self._raw_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._grid_cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    # -- construction -----------------------------------------------------

    @classmethod
    def create(cls, path: str | Path) -> "SpectralLibrary":
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        index_path = path / "index.json"
        if not index_path.exists():
            index_path.write_text(json.dumps({"entries": []}))
        return cls.open(path)

    @classmethod
    def open(cls, path: str | Path) -> "SpectralLibrary":
        path = Path(path)
        index_path = path / "index.json"
        if not index_path.exists():
            raise FileNotFoundError(f"no spectral library index found at {index_path}")
        data = json.loads(index_path.read_text())
        return cls(path, data.get("entries", []))

    def _save_index(self) -> None:
        (self.path / "index.json").write_text(json.dumps({"entries": self._index}, indent=2))

    def _invalidate(self) -> None:
        self._raw_cache.clear()
        self._grid_cache.clear()

    # -- mutation -----------------------------------------------------------

    def add(self, spectrum: Spectrum, name: str | None = None, **meta: Any) -> str:
        """Add a spectrum to the library, returning its (possibly deduplicated) name."""
        base_name = name or spectrum.title or f"spectrum_{len(self._index) + 1}"
        existing = {e["name"] for e in self._index}
        final_name = base_name
        i = 2
        while final_name in existing:
            final_name = f"{base_name}_{i}"
            i += 1

        slug = _slugify(final_name)
        filename = f"{slug}.npz"
        while (self.path / filename).exists():
            filename = f"{slug}_{i}.npz"
            i += 1

        full_meta = {**spectrum.meta, **meta}
        np.savez(
            self.path / filename,
            x=spectrum.x,
            y=spectrum.y,
            x_unit=spectrum.x_unit.value,
            y_unit=spectrum.y_unit.value,
            meta_json=json.dumps(full_meta),
        )
        self._index.append({"name": final_name, "file": filename, "meta": full_meta})
        self._save_index()
        self._invalidate()
        return final_name

    def remove(self, name: str) -> None:
        for i, entry in enumerate(self._index):
            if entry["name"] == name:
                npz_path = self.path / entry["file"]
                if npz_path.exists():
                    npz_path.unlink()
                del self._index[i]
                self._save_index()
                self._invalidate()
                return
        raise KeyError(f"no such library entry: {name!r}")

    # -- access -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._index)

    def __iter__(self) -> Iterator[str]:
        return (e["name"] for e in self._index)

    def get(self, name: str) -> Spectrum:
        for entry in self._index:
            if entry["name"] == name:
                data = np.load(self.path / entry["file"], allow_pickle=False)
                return Spectrum(
                    x=data["x"],
                    y=data["y"],
                    x_unit=XUnit(str(data["x_unit"])),
                    y_unit=YUnit(str(data["y_unit"])),
                    title=name,
                    meta=dict(entry.get("meta", {})),
                )
        raise KeyError(f"no such library entry: {name!r}")

    # -- internal: cached raw arrays + grid matrices -----------------------

    def _raw(self, name: str) -> tuple[np.ndarray, np.ndarray, str]:
        if name not in self._raw_cache:
            entry = next(e for e in self._index if e["name"] == name)
            data = np.load(self.path / entry["file"], allow_pickle=False)
            self._raw_cache[name] = (np.asarray(data["x"], dtype=float), np.asarray(data["y"], dtype=float), str(data["y_unit"]))
        x, y, y_unit = self._raw_cache[name]
        return x, y, y_unit

    def _grid_matrix(self, grid: np.ndarray, preprocess: bool) -> tuple[list[str], np.ndarray]:
        key = (round(grid[0], 6), round(grid[-1], 6), len(grid), preprocess)
        names = list(self)
        if key in self._grid_cache:
            cached_names, matrix = self._grid_cache[key]
            if cached_names == names:
                return cached_names, matrix

        rows = np.empty((len(names), len(grid)))
        for i, name in enumerate(names):
            x, y, y_unit_str = self._raw(name)
            if preprocess and y_unit_str == YUnit.TRANSMITTANCE.value:
                t = np.clip(y / 100.0, 1e-8, None)
                y = -np.log10(t)
            rows[i] = interp_values(grid, x, y)
        self._grid_cache[key] = (names, rows)
        return names, rows


@dataclass
class Match:
    name: str
    score: float
    spectrum: Spectrum


def _default_grid(library: SpectralLibrary, region: tuple[float, float] | None) -> np.ndarray:
    if region is not None:
        lo, hi = (region[0], region[1]) if region[0] <= region[1] else (region[1], region[0])
    else:
        lo, hi = _DEFAULT_X_RANGE
    return np.linspace(lo, hi, _DEFAULT_GRID_POINTS)


def _mean_center_normalize(matrix: np.ndarray) -> np.ndarray:
    centered = matrix - matrix.mean(axis=-1, keepdims=True)
    norms = np.linalg.norm(centered, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return centered / norms


def search(
    query: Spectrum,
    library: SpectralLibrary,
    metric: str = "correlation",
    region: tuple[float, float] | None = None,
    top: int = 10,
    preprocess: bool = True,
) -> list[Match]:
    """Search ``library`` for the entries most similar to ``query``.

    ``metric`` is one of "correlation", "euclidean" or
    "first-derivative-correlation". Higher scores are always better (for
    "euclidean" the score is the negated distance). The library's per-entry
    grid matrix is cached (keyed by grid range/size and ``preprocess``), so
    repeated searches over the same region are fast even for large libraries.
    """
    if len(library) == 0:
        return []
    if metric not in ("correlation", "euclidean", "first-derivative-correlation"):
        raise ValueError(f"unknown metric: {metric!r}")

    grid = _default_grid(library, region)
    names, matrix = library._grid_matrix(grid, preprocess)

    if preprocess and query.y_unit == YUnit.TRANSMITTANCE:
        query_y = to_absorbance(query).y
    else:
        query_y = query.y
    query_on_grid = interp_values(grid, query.x, query_y)

    if metric == "first-derivative-correlation":
        work_matrix = np.gradient(matrix, axis=-1)
        work_query = np.gradient(query_on_grid)
    else:
        work_matrix = matrix
        work_query = query_on_grid

    if metric in ("correlation", "first-derivative-correlation"):
        norm_matrix = _mean_center_normalize(work_matrix)
        norm_query = _mean_center_normalize(work_query[np.newaxis, :])[0]
        scores = norm_matrix @ norm_query
    else:  # euclidean
        diff = work_matrix - work_query[np.newaxis, :]
        scores = -np.linalg.norm(diff, axis=-1)

    order = np.argsort(scores)[::-1][:top]
    return [Match(name=names[i], score=float(scores[i]), spectrum=library.get(names[i])) for i in order]
