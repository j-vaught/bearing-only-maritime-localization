"""Tiny GPU3 CLI lifecycle check for the shared physical-aid object policy."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely import to_geojson
from shapely.geometry import box

from maritime.catalog_identity import canonical_aid_key
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.gpu.resident.cache import file_digest
from maritime.serialization import write_contract


def test_cli_reuses_raw_map_and_object_view_for_optimized_refinement(tmp_path):
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() <= 3:
        pytest.skip("GPU3 required to preserve the current run")
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-79.905, 32.77)
    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.905, 32.77, 0, 1000)
    stamp = "2023-07-15T12:00:30Z"
    rows = tuple(
        CatalogRecordV2(
            k,
            lat,
            lon,
            normalized_type=label,
            metadata=(
                ("position_uncertainty_m", 0.0),
                ("visible_feature_offset_east_m", 0.0),
                ("visible_feature_offset_north_m", 0.0),
            ),
        )
        for k, label in (("beacon", "beacon"), ("light", "light"), ("daymark", "daymark"))
    )
    catalog, map_file, measurements = (
        tmp_path / name for name in ("catalog.json", "map.json", "measurements.json")
    )
    coverage = to_geojson(box(x - 30000, y - 30000, x + 30000, y + 30000))
    write_contract(catalog, CatalogV2(rows, stamp, "test", "1", "aid-cli"))
    write_contract(
        map_file,
        MapV2(
            crs="EPSG:32618",
            source="test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=coverage,
        ),
    )
    write_contract(
        measurements, MeasurementSetV2((MeasurementV2(0, stamp, object_type="physical_aid"),))
    )
    settings = {
        "map": str(map_file),
        "catalog": str(catalog),
        "measurements": str(measurements),
        "marker_tolerance_deg": 0.5,
        "preparation": {
            "chart_geometry_geojson": json.loads(coverage),
            "catalog_complete": True,
            "catalog_objects_cover_envelope": True,
            "identities_certified": True,
            "exact_positions_asserted": True,
            "nominal_offsets_asserted": True,
            "max_projection_scale": 2.0,
            "azimuth_error_deg": 30.0,
        },
        "search": {
            "bounds_en_m": [x - 0.5, x + 0.5, y - 0.5, y + 0.5],
            "devices": [3],
            "initial_size_m": 1,
            "terminal_size_m": 0.5,
            "object_capacity": 8,
            "batch_cells": 8,
            "roots_per_task": 1,
            "trusted_marker_labels": True,
            "hard_bearing_bounds_asserted": True,
            "numerical_validation_accepted": True,
        },
    }
    config = tmp_path / "settings.json"
    config.write_text(json.dumps(settings))
    root = Path(__file__).resolve().parents[1]
    common = [
        "--execute",
        "--settings",
        str(config),
        "--cache-dir",
        str(tmp_path / "base"),
        "--object-cache-dir",
        str(tmp_path / "objects"),
    ]
    first = tmp_path / "first"
    process = subprocess.run(
        [sys.executable, "tools/run_resident_marker_search.py", *common, "--output", str(first)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    entries = list((tmp_path / "base").glob("*.npz"))
    assert len(entries) == 1
    base = entries[0]
    saved = (file_digest(base), base.stat().st_mtime_ns)
    second = tmp_path / "second"
    # Construction of a fresh map/catalog index must be skipped on the second invocation.
    script = """import sys
from pathlib import Path
import maritime.search.gpu.resident.preparation as preparation
import maritime.search.gpu.resident.aid_view as aid_view
import maritime.search.gpu.resident.cache as base_cache
def forbidden(*args, **kwargs): raise AssertionError("Rebuilt cached index")
preparation.prepare_world=forbidden
aid_view._build_view=forbidden
from tools.run_resident_marker_search import main
main()
"""
    process = subprocess.run(
        [sys.executable, "-c", script, *common, "--optimized", "--output", str(second)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert (file_digest(base), base.stat().st_mtime_ns) == saved
    assert json.loads((second / "prepared-cache.json").read_text())["hit"]
    assert json.loads((second / "canonical-object-cache.json").read_text())["hit"]
    summary = json.loads((second / "summary.json").read_text())
    assert summary["complete"] and summary["terminal"] == 4 and summary["evaluated"] == 5
    manifest = json.loads((second / "manifest.json").read_text())
    assert manifest["object_keys"] == [canonical_aid_key(lat, lon)]
    leaves = list(second.rglob("leaf-*.npz"))
    assert leaves
    for file in leaves:
        with np.load(file) as results:
            assert np.all(results["status"] == 0) and np.all(results["flags"] == 0)
