import numpy as np
import pytest

from openftir.processing import SpectralLibrary, search

from test_processing_synth import make_absorbance_spectrum


@pytest.fixture
def library(tmp_path):
    lib = SpectralLibrary.create(tmp_path / "lib")
    rng = np.random.default_rng(42)
    target_bands = [(1050.0, 0.8, 20.0), (1650.0, 0.5, 15.0), (2900.0, 0.6, 25.0)]
    lib.add(make_absorbance_spectrum(bands=target_bands, noise=0.0, seed=1), name="target")
    for i in range(15):
        center = 500 + rng.uniform(0, 3000)
        distractor_bands = [(center, rng.uniform(0.2, 1.0), rng.uniform(10, 40))]
        lib.add(make_absorbance_spectrum(bands=distractor_bands, noise=0.01, seed=100 + i), name=f"distractor_{i}")
    return lib, target_bands


def test_library_create_add_len_iter_get(tmp_path):
    lib = SpectralLibrary.create(tmp_path / "lib2")
    assert len(lib) == 0
    s = make_absorbance_spectrum(noise=0.0)
    name = lib.add(s, name="sample_a")
    assert name == "sample_a"
    assert len(lib) == 1
    assert list(lib) == ["sample_a"]
    fetched = lib.get("sample_a")
    np.testing.assert_allclose(fetched.y, s.y, atol=1e-9)


def test_library_open_round_trip(tmp_path):
    path = tmp_path / "lib3"
    lib = SpectralLibrary.create(path)
    lib.add(make_absorbance_spectrum(noise=0.0), name="a")
    reopened = SpectralLibrary.open(path)
    assert len(reopened) == 1
    assert list(reopened) == ["a"]


def test_library_remove(tmp_path):
    lib = SpectralLibrary.create(tmp_path / "lib4")
    lib.add(make_absorbance_spectrum(noise=0.0), name="a")
    lib.add(make_absorbance_spectrum(noise=0.0), name="b")
    lib.remove("a")
    assert len(lib) == 1
    assert list(lib) == ["b"]
    with pytest.raises(KeyError):
        lib.remove("nonexistent")


def test_library_dedup_name(tmp_path):
    lib = SpectralLibrary.create(tmp_path / "lib5")
    lib.add(make_absorbance_spectrum(noise=0.0), name="dup")
    name2 = lib.add(make_absorbance_spectrum(noise=0.0), name="dup")
    assert name2 != "dup"
    assert len(lib) == 2


def test_search_ranks_correct_entry_first(library):
    lib, target_bands = library
    query = make_absorbance_spectrum(bands=target_bands, noise=0.02, seed=999)
    matches = search(query, lib, metric="correlation", top=5)
    assert len(matches) == 5
    assert matches[0].name == "target"
    # scores should be descending
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)


def test_search_euclidean_and_derivative_metrics_also_rank_target_first(library):
    lib, target_bands = library
    query = make_absorbance_spectrum(bands=target_bands, noise=0.02, seed=1000)
    for metric in ("euclidean", "first-derivative-correlation"):
        matches = search(query, lib, metric=metric, top=3)
        assert matches[0].name == "target", f"metric={metric} failed to rank target first"


def test_search_region_limits_comparison(library):
    lib, target_bands = library
    query = make_absorbance_spectrum(bands=target_bands, noise=0.02, seed=1001)
    matches = search(query, lib, metric="correlation", region=(1000.0, 1100.0), top=3)
    assert matches[0].name == "target"


def test_search_empty_library_returns_empty(tmp_path):
    lib = SpectralLibrary.create(tmp_path / "empty")
    query = make_absorbance_spectrum()
    assert search(query, lib) == []


def test_search_unknown_metric_raises(library):
    lib, _ = library
    query = make_absorbance_spectrum()
    with pytest.raises(ValueError):
        search(query, lib, metric="bogus")
