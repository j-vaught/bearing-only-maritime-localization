"""Inspect or explicitly execute the experimental resident GPU marker search.

Without --execute this prints the contract without data preparation or CUDA.
--execute must only be used after the user approves the specific run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CONTRACT = {
    "status": "synthetic_checks_passed_coastal_validation_pending",
    "cpu_preparation": ["read_inputs", "project_fixed_catalog", "build_spatial_indices"],
    "gpu_search": [
        "seed_cells",
        "map_classification",
        "region_marker_pruning",
        "region_land_blocking",
        "subdivide_and_compact",
        "ellipsoidal_prediction",
        "visibility",
        "circular_grouping",
        "injective_marker_matching",
    ],
    "cpu_during_search": ["kernel_launch", "root_task_dispatch", "scalar_progress", "disk_io"],
    "cpu_query_fallback": False,
    "chart_aid_identity": "one object per exact chart coordinate, shared with simulator",
    "canonical_object_cache": "separate object-only sidecar; original map cache reused",
    "accuracy_claim": None,
    "gpu_performance_claim": None,
    "approved_runs": ["small_synthetic_checks"],
}


def check_resolution(initial_m: float, stopping_m: float, coarse_only: bool) -> None:
    if stopping_m >= initial_m and not coarse_only:
        raise ValueError(
            "Equal initial and stopping sizes disable refinement. "
            "Choose a smaller terminal_size_m or explicitly pass --coarse-only."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/resident-world"))
    parser.add_argument(
        "--object-cache-dir",
        type=Path,
        help="Separate canonical object cache; defaults beside the resolved base cache",
    )
    parser.add_argument(
        "--optimized",
        action="store_true",
        help="Use true-bearing pruning, parallel prediction and cooperative matching",
    )
    parser.add_argument(
        "--coarse-only",
        action="store_true",
        help="Explicitly allow stopping at the initial cell size",
    )
    parser.add_argument("--execute", action="store_true", help="Execute only after user approval")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(CONTRACT, indent=2))
        return
    if args.settings is None or args.output is None:
        parser.error("Execution requires --settings and a new --output directory.")
    # Input preparation and CUDA imports are below the explicit execution gate.
    from time import perf_counter

    import numpy as np
    from shapely.geometry import shape

    from maritime.geometry.vector_map import VectorMap
    from maritime.schemas.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
    from maritime.schemas.object_types import normalize_object_type
    from maritime.search.gpu.resident.cache import load_or_prepare, preparation_identity
    from maritime.search.gpu.resident.engine import ResidentConfig, run_resident_search
    from maritime.search.gpu.resident.preparation import prepare_world
    from maritime.serialization import read_contract

    preparation_started = perf_counter()
    print(json.dumps({"stage": "input_loading_cpu"}), flush=True)
    settings = json.loads(args.settings.read_text())
    if set(settings) - {
        "map",
        "catalog",
        "measurements",
        "search",
        "preparation",
        "marker_tolerance_deg",
    }:
        raise ValueError("Unexpected settings field; observer truth is not an accepted input.")
    config = ResidentConfig(**settings["search"])
    check_resolution(config.initial_size_m, config.terminal_size_m, args.coarse_only)
    print(
        json.dumps(
            {
                "stage": "configured_resolution",
                "initial_cell_size_m": config.initial_size_m,
                "stopping_cell_size_m": config.terminal_size_m,
                "coarse_only": args.coarse_only,
            }
        ),
        flush=True,
    )
    tolerance = settings["marker_tolerance_deg"]
    measurements = read_contract(Path(settings["measurements"]), MeasurementSetV2)
    if (
        measurements.calibrated_common_bias_mean_deg is not None
        or measurements.calibrated_common_bias_sigma_deg is not None
        or measurements.covariance_deg2 is not None
    ):
        raise ValueError("Correlated or biased bearings are unsupported.")
    markers = [
        m
        for m in measurements.measurements
        if normalize_object_type(m.object_type) == "physical_aid"
    ]
    preparation_options = dict(settings.get("preparation", {}))
    print(json.dumps({"stage": "prepared_cache_lookup_cpu"}), flush=True)
    cache_key = preparation_identity(
        Path(settings["map"]),
        Path(settings["catalog"]),
        preparation_options,
    )

    def builder():
        print(json.dumps({"stage": "cache_miss_map_loading_cpu"}), flush=True)
        catalog = read_contract(Path(settings["catalog"]), CatalogV2)
        map_input = read_contract(Path(settings["map"]), MapV2)
        vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
        preparation = dict(preparation_options)
        if "chart_geometry_geojson" in preparation:
            preparation["chart_geometry"] = shape(preparation.pop("chart_geometry_geojson"))
        print(
            json.dumps(
                {
                    "stage": "spatial_index_preparation_cpu",
                    "markers": len(markers),
                    "catalog_records": len(catalog.records),
                }
            ),
            flush=True,
        )
        return prepare_world(vector_map, catalog, **preparation)

    world, cache_hit = load_or_prepare(args.cache_dir, cache_key, builder)
    # Reuse the original expensive map/index preparation unchanged. The separate
    # logical object view has its own source/catalog/policy identity and cache.
    from maritime.search.gpu.resident.aid_view import load_or_canonicalize
    from maritime.search.gpu.resident.optimized import OptimizedResidentDevice

    object_cache_dir = (
        args.object_cache_dir or args.cache_dir.resolve().parent / "resident-aid-objects"
    )
    source_catalog = read_contract(Path(settings["catalog"]), CatalogV2)
    world, object_cache_hit, object_cache_key = load_or_canonicalize(
        object_cache_dir, cache_key, world, source_catalog
    )
    print(
        json.dumps(
            {
                "stage": "canonical_objects_ready",
                "cache_hit": object_cache_hit,
                "cache_key": object_cache_key,
                "objects": len(world.objects),
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {"stage": "prepared_world_ready", "cache_hit": cache_hit, "cache_key": cache_key}
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "stage": "gpu_search_starting",
                "preparation_s": perf_counter() - preparation_started,
                "objects": len(world.objects),
            }
        ),
        flush=True,
    )
    summary = run_resident_search(
        world,
        config,
        np.asarray([m.bearing_deg for m in markers]),
        np.full(len(markers), tolerance, dtype=np.float64),
        args.output,
        device_factory=OptimizedResidentDevice if args.optimized else None,
    )
    (args.output / "prepared-cache.json").write_text(
        json.dumps({"key": cache_key, "hit": cache_hit, "directory": str(args.cache_dir)}, indent=2)
        + "\n"
    )
    (args.output / "canonical-object-cache.json").write_text(
        json.dumps(
            {
                "key": object_cache_key,
                "hit": object_cache_hit,
                "directory": str(object_cache_dir),
                "optimized": args.optimized,
            },
            indent=2,
        )
        + "\n"
    )
    (args.output / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    hashes = {
        k: hashlib.sha256(Path(settings[k]).read_bytes()).hexdigest()
        for k in ("map", "catalog", "measurements")
    }
    (args.output / "input-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
