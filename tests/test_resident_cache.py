"""Pending approval. Cache and stopping-rule checks have not been executed."""

import numpy as np
import pytest

from maritime.search.gpu.resident.cache import load_or_prepare, preparation_identity
from maritime.search.gpu.resident.preparation import PackedTree, PreparedWorld
from tools.run_resident_marker_search import check_resolution


def _world():
    edge = PackedTree(np.asarray([[0., 0., 1., 1.]]),
                      np.asarray([[-1, -1]], dtype=np.int32),
                      np.asarray([[0., 0., 1., 1.]]))
    objects = np.asarray([[1., 2., -79., 33., 0., 0., 1., 1.]])
    tree = PackedTree(np.asarray([[1., 2., 1., 2.]]),
                      np.asarray([[-1, -1]], dtype=np.int32), objects)
    return PreparedWorld((edge, edge, edge, edge), objects, ("aid",), tree,
                         np.empty((0, 4)), True, True, False, False, True, 1.1, 10.)


def test_cache_roundtrip_skips_preparation_on_second_request(tmp_path):
    key = "a" * 64
    world = _world()
    first, hit = load_or_prepare(tmp_path, key, lambda: world)
    assert not hit
    assert first is world

    def forbidden_builder():
        raise AssertionError("Preparation was rebuilt on a matching cache hit")

    second, hit = load_or_prepare(tmp_path, key, forbidden_builder)
    assert hit
    assert second.object_keys == world.object_keys
    assert second.max_projection_scale == world.max_projection_scale
    assert second.chart_scope_certified == world.chart_scope_certified
    np.testing.assert_array_equal(second.objects, world.objects)
    np.testing.assert_array_equal(second.object_tree.children, world.object_tree.children)
    for original, restored in zip(world.layers, second.layers, strict=True):
        np.testing.assert_array_equal(original.items, restored.items)
        np.testing.assert_array_equal(original.bounds, restored.bounds)
        np.testing.assert_array_equal(original.children, restored.children)


def test_input_or_preparation_change_invalidates_cache_key(tmp_path):
    map_path, catalog_path = tmp_path / "map.json", tmp_path / "catalog.json"
    map_path.write_text("map-one")
    catalog_path.write_text("catalog-one")
    key = preparation_identity(map_path, catalog_path, {"catalog_complete": False})
    assert key == preparation_identity(map_path, catalog_path, {"catalog_complete": False})
    assert key != preparation_identity(map_path, catalog_path, {"catalog_complete": True})
    catalog_path.write_text("catalog-two")
    assert key != preparation_identity(map_path, catalog_path, {"catalog_complete": False})


def test_coarse_only_stop_requires_explicit_request():
    with pytest.raises(ValueError, match="disable refinement"):
        check_resolution(10000., 10000., False)
    check_resolution(10000., 10000., True)
    check_resolution(10000., 100., False)
