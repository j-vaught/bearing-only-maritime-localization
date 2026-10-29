"""Only the reviewed CPU query-index correction may reuse its preparation key."""

import inspect
from pathlib import Path

import pytest

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.resident import cache


def _inputs(tmp_path):
    map_path, catalog_path = tmp_path / "map.json", tmp_path / "catalog.json"
    map_path.write_text("unchanged-map")
    catalog_path.write_text("unchanged-catalog")
    return map_path, catalog_path


def test_exact_index_fix_preserves_legacy_preparation_identity(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    vector_path = Path(inspect.getfile(VectorMap)).resolve()
    original_digest = cache.file_digest
    assert original_digest(vector_path) == cache._FIXED_VECTOR_MAP_SHA256
    corrected_key = cache.preparation_identity(*paths, {})

    def legacy_digest(path):
        if Path(path).resolve() == vector_path:
            return cache._LEGACY_VECTOR_MAP_SHA256
        return original_digest(path)

    monkeypatch.setattr(cache, "file_digest", legacy_digest)
    assert corrected_key == cache.preparation_identity(*paths, {})


@pytest.mark.parametrize("future_digest", ["1" * 64, "e" * 64])
def test_unlisted_vector_map_revisions_invalidate_preparation(tmp_path, monkeypatch, future_digest):
    paths = _inputs(tmp_path)
    vector_path = Path(inspect.getfile(VectorMap)).resolve()
    original_digest = cache.file_digest
    corrected_key = cache.preparation_identity(*paths, {})

    def changed_digest(path):
        if Path(path).resolve() == vector_path:
            return future_digest
        return original_digest(path)

    monkeypatch.setattr(cache, "file_digest", changed_digest)
    assert cache.preparation_identity(*paths, {}) != corrected_key


def test_compatibility_is_restricted_to_vector_map_file(tmp_path, monkeypatch):
    other_source = tmp_path / "other_preparation.py"
    other_source.write_text("not-the-vector-map-module")
    monkeypatch.setattr(cache, "file_digest", lambda path: cache._FIXED_VECTOR_MAP_SHA256)
    assert cache._preparation_source_digest(other_source) == cache._FIXED_VECTOR_MAP_SHA256


@pytest.mark.parametrize(
    "primitive,current,legacy",
    [
        (
            cache._visible_feature_offset,
            cache._PREDICTION_RUNTIME_SHA256,
            cache._PREDICTION_BASE_SHA256,
        ),
        (
            cache._effective_position_uncertainty,
            cache._SCORING_RUNTIME_SHA256,
            cache._SCORING_BASE_SHA256,
        ),
    ],
)
def test_runtime_only_revision_preserves_raw_base_cache_and_future_changes_invalidate(
    tmp_path, monkeypatch, primitive, current, legacy
):
    paths = _inputs(tmp_path)
    source = Path(inspect.getfile(primitive)).resolve()
    original_digest = cache.file_digest
    assert original_digest(source) == current
    key = cache.preparation_identity(*paths, {})

    def digest_for(value):
        return lambda path: value if Path(path).resolve() == source else original_digest(path)

    monkeypatch.setattr(cache, "file_digest", digest_for(legacy))
    assert cache.preparation_identity(*paths, {}) == key
    monkeypatch.setattr(cache, "file_digest", digest_for("f" * 64))
    assert cache.preparation_identity(*paths, {}) != key
