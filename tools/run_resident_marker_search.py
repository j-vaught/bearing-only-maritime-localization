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
    "accuracy_claim": None,
    "gpu_performance_claim": None,
    "approved_runs": ["small_synthetic_checks"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true", help="Execute only after user approval")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(CONTRACT, indent=2))
        return
    if args.settings is None or args.output is None:
        parser.error("Execution requires --settings and a new --output directory.")
    # Input preparation and CUDA imports are below the explicit execution gate.
    import numpy as np
    from shapely.geometry import shape

    from maritime.geometry.vector_map import VectorMap
    from maritime.schemas.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
    from maritime.schemas.object_types import normalize_object_type
    from maritime.search.gpu.resident.engine import ResidentConfig, run_resident_search
    from maritime.search.gpu.resident.preparation import prepare_world
    from maritime.serialization import read_contract

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
    catalog = read_contract(Path(settings["catalog"]), CatalogV2)
    map_input = read_contract(Path(settings["map"]), MapV2)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    preparation = dict(settings.get("preparation", {}))
    if "chart_geometry_geojson" in preparation:
        preparation["chart_geometry"] = shape(preparation.pop("chart_geometry_geojson"))
    world = prepare_world(vector_map, catalog, **preparation)
    summary = run_resident_search(
        world,
        config,
        np.asarray([m.bearing_deg for m in markers]),
        np.full(len(markers), tolerance, dtype=np.float64),
        args.output,
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
