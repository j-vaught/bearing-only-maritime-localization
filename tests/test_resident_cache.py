"""Approved small cache reuse, stopping-rule and GPU refinement checks."""

import numpy as np
import pytest

from maritime.search.gpu.resident.cache import load_or_prepare, preparation_identity
from maritime.search.gpu.resident.preparation import PackedTree, PreparedWorld
from tools.run_resident_marker_search import check_resolution


def _world():
    edge = PackedTree(
        np.asarray([[0.0, 0.0, 1.0, 1.0]]),
        np.asarray([[-1, -1]], dtype=np.int32),
        np.asarray([[0.0, 0.0, 1.0, 1.0]]),
    )
    objects = np.asarray([[1.0, 2.0, -79.0, 33.0, 0.0, 0.0, 1.0, 1.0]])
    tree = PackedTree(
        np.asarray([[1.0, 2.0, 1.0, 2.0]]), np.asarray([[-1, -1]], dtype=np.int32), objects
    )
    return PreparedWorld(
        (edge, edge, edge, edge),
        objects,
        ("aid",),
        tree,
        np.empty((0, 4)),
        True,
        True,
        False,
        False,
        True,
        1.1,
        10.0,
    )


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
        check_resolution(10000.0, 10000.0, False)
    check_resolution(10000.0, 10000.0, True)
    check_resolution(10000.0, 100.0, False)


def test_separate_cli_process_reuses_real_index_and_refines_automatically(tmp_path):
    import json
    import subprocess
    import sys

    from pyproj import Geod, Transformer
    from shapely.geometry import box, mapping

    from maritime.schemas.inputs_v2 import (
        CatalogRecordV2,
        CatalogV2,
        MapV2,
        MeasurementSetV2,
        MeasurementV2,
    )
    from maritime.search.gpu.resident.cache import file_digest
    from maritime.serialization import write_contract

    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA is required for the refinement integration check")
    except cp.cuda.runtime.CUDARuntimeError as exc:
        pytest.skip(str(exc))

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-79.905, 32.77)
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 90.0, 1000.0)
    coverage = mapping(box(x - 30000, y - 30000, x + 30000, y + 30000))
    stamp = "2023-07-15T12:00:30Z"
    catalog_path, map_path, measurements_path = (
        tmp_path / "catalog.json",
        tmp_path / "map.json",
        tmp_path / "measurements.json",
    )
    write_contract(
        catalog_path,
        CatalogV2(
            (CatalogRecordV2("aid", lat, lon, normalized_type="physical_aid"),),
            stamp,
            "small cache test",
            "1",
            "cache-test",
        ),
    )
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:32618",
            source="small cache test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(coverage),
        ),
    )
    write_contract(
        measurements_path,
        MeasurementSetV2(
            (MeasurementV2(90.0, stamp, sigma_deg=1 / 6, object_type="physical_aid"),)
        ),
    )
    settings = {
        "map": str(map_path),
        "catalog": str(catalog_path),
        "measurements": str(measurements_path),
        "marker_tolerance_deg": 0.2,
        "preparation": {
            "chart_geometry_geojson": coverage,
            "catalog_complete": True,
            "catalog_objects_cover_envelope": True,
            "identities_certified": True,
            "exact_positions_asserted": True,
            "nominal_offsets_asserted": True,
            "max_projection_scale": 1.02,
            "azimuth_error_deg": 10.0,
        },
        "search": {
            "bounds_en_m": [x - 2, x + 2, y - 2, y + 2],
            "devices": [0],
            "initial_size_m": 4.0,
            "terminal_size_m": 2.0,
            "batch_cells": 8,
            "roots_per_task": 1,
            "object_capacity": 32,
            "trusted_marker_labels": True,
            "hard_bearing_bounds_asserted": True,
            "numerical_validation_accepted": True,
        },
    }
    child = r"""
import json, sys
from pathlib import Path
import maritime.search.gpu.resident.cache  # Bind the unmodified source fingerprints first.
import maritime.search.gpu.resident.preparation as preparation
from maritime.search.gpu.resident.engine import ResidentDevice
import maritime.serialization as serialization
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import MapV2, CatalogV2
from tools.run_resident_marker_search import main

settings, output, cache, counter, mode = sys.argv[1:]
original_prepare = preparation.prepare_world
original_read = serialization.read_contract
split_steps = []
original_split = ResidentDevice.split

def prepare(*args, **kwargs):
    if mode == "reuse":
        raise AssertionError("Index was rebuilt in the second process")
    with Path(counter).open("a") as stream:
        stream.write("prepare\n")
    return original_prepare(*args, **kwargs)

def read(path, model):
    if mode == "reuse" and model in (MapV2, CatalogV2):
        raise AssertionError("Map/catalog were parsed again on a cache hit")
    return original_read(path, model)

def forbidden(*args, **kwargs):
    raise AssertionError("CPU map query during cached GPU refinement")

def split(self, cells):
    if len(cells):
        width = float(self.cp.max(cells[:,2:]-cells[:,:2]).item())
        split_steps.append({"parent_count": len(cells), "parent_width_m": width})
    return original_split(self, cells)

preparation.prepare_world = prepare
serialization.read_contract = read
ResidentDevice.split = split
if mode == "reuse":
    VectorMap.point_water_status = forbidden
    VectorMap.line_visibility = forbidden
    VectorMap.from_map_v2 = classmethod(forbidden)
sys.argv = ["small-cache-check", "--execute", "--settings", settings,
            "--output", output, "--cache-dir", cache]
main()
Path(output, "observed-splits.json").write_text(json.dumps(split_steps))
"""
    settings_file = tmp_path / "settings.json"
    cache_dir, counter = tmp_path / "cache", tmp_path / "preparation-calls.log"
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    settings_file.write_text(json.dumps(settings))
    subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(settings_file),
            str(first_dir),
            str(cache_dir),
            str(counter),
            "build",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    cache_file = next(cache_dir.glob("*.npz"))
    before_hash, before_mtime = file_digest(cache_file), cache_file.stat().st_mtime_ns

    settings["search"]["terminal_size_m"] = 1.0
    settings_file.write_text(json.dumps(settings))
    subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(settings_file),
            str(second_dir),
            str(cache_dir),
            str(counter),
            "reuse",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    first_cache = json.loads((first_dir / "prepared-cache.json").read_text())
    second_cache = json.loads((second_dir / "prepared-cache.json").read_text())
    first_summary = json.loads((first_dir / "summary.json").read_text())
    second_summary = json.loads((second_dir / "summary.json").read_text())
    splits = json.loads((second_dir / "observed-splits.json").read_text())
    assert first_cache["hit"] is False and second_cache["hit"] is True
    assert first_cache["key"] == second_cache["key"]
    assert counter.read_text().splitlines() == ["prepare"]
    assert file_digest(cache_file) == before_hash and cache_file.stat().st_mtime_ns == before_mtime
    assert first_summary["complete"] and first_summary["terminal"] == 4
    assert second_summary["complete"] and second_summary["terminal"] == 16
    assert second_summary["evaluated"] == 21
    assert [s["parent_width_m"] for s in splits] == [4.0, 2.0]
    assert [s["parent_count"] for s in splits] == [1, 4]
    blocks = [np.load(p, allow_pickle=False) for p in second_dir.rglob("leaf-*.npz")]
    try:
        assert sum(len(block["cells"]) for block in blocks) == 16
        assert all(np.all(block["cells"][:, 2:] - block["cells"][:, :2] <= 1.0) for block in blocks)
    finally:
        for block in blocks:
            block.close()
    report = {
        "scope": "Two-process tiny-index cache and GPU refinement check; no coast-wide run",
        "preparation_calls_across_processes": 1,
        "first_cache_hit": first_cache["hit"],
        "second_cache_hit": second_cache["hit"],
        "cache_key_unchanged_after_resolution_change": True,
        "cache_file_bytes_and_mtime_unchanged_on_hit": True,
        "map_catalog_parsing_and_preparation_forbidden_in_second_process": True,
        "cpu_map_queries_forbidden_in_second_process": True,
        "automatic_split_steps_in_second_invocation": splits,
        "first_summary": first_summary,
        "second_summary": second_summary,
        "all_16_terminal_cells_at_or_below_1m": True,
    }
    (tmp_path / "cache-refinement-verification.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
