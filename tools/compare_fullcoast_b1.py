"""One-row reproduction of the public eastern-seaboard B1 search."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import runpy
import sys
from pathlib import Path
from time import perf_counter

import numpy as np


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    sys.path.insert(0, str(root))
    driver = runpy.run_path(str(root / "tools/benchmark_eastern_seaboard_pruning.py"))
    pilot = runpy.run_path(str(root / "tools/run_ais_enc_pilot.py"))
    benchmark = runpy.run_path(str(root / "tools/benchmark_eastern_seaboard_search.py"))
    _, _, sites, _ = pilot["load_inputs"]()
    _, _, snapshots, coast_bounds, aid_counts = benchmark["build_inputs"]()
    source_catalog = snapshots["all_scales_deduplicated"]
    from bearing_localization.scene import (
        AISRecord as SceneAISRecord,
    )
    from bearing_localization.scene import (
        Landmark,
        Scene,
        SceneConfig,
        ShipPose,
    )
    from bearing_localization.search import HierarchicalSearchConfig, run_hierarchical_search
    from bearing_localization.simulator.runner import run_simulator
    from maritime_localization_shared.contracts import AISRecord, AISSnapshot
    from maritime_localization_shared.geospatial.water_mask import WaterMask

    catalog = AISSnapshot(
        records=tuple(
            AISRecord(
                record_id=f"catalog:{i}",
                east_m=r.east_m,
                north_m=r.north_m,
                ais_class=r.ais_class,
                source_type_code=r.source_type_code,
            )
            for i, r in enumerate(source_catalog.records)
        ),
        timestamp_utc=source_catalog.timestamp_utc,
        crs=source_catalog.crs,
        snapshot_id=source_catalog.snapshot_id,
    )
    packed = np.load(driver["WATER_PATH"], allow_pickle=False)
    mask = WaterMask(
        packed["water"].astype(bool),
        float(packed["west_m"]),
        float(packed["south_m"]),
        float(packed["cell_size_m"]),
    )
    visibility = driver["CoastVisibility"](mask)
    landmarks = tuple(
        Landmark(
            object_id=i,
            east_m=r.east_m,
            north_m=r.north_m,
            object_type=r.ais_class or "unknown",
            has_ais=r.ais_class == "vessel",
        )
        for i, r in enumerate(catalog.records)
    )
    scene_ais = tuple(
        SceneAISRecord(i, r.east_m, r.north_m, r.ais_class or "unknown")
        for i, r in enumerate(catalog.records)
        if r.ais_class == "vessel"
    )
    scene = Scene(
        ais=scene_ais,
        landmarks=landmarks,
        config=SceneConfig(
            width_m=coast_bounds[1] - coast_bounds[0],
            height_m=coast_bounds[3] - coast_bounds[2],
            object_count=len(landmarks),
            ais_fraction=len(scene_ais) / len(landmarks),
            visibility_radius_m=driver["RANGE_M"],
            bearing_noise_sigma_deg=0.5,
            report_type_labels=True,
            object_types=("vessel", "aid"),
        ),
    )
    site_index = list(sites).index("B1")
    east, north = sites["B1"]
    if not visibility.is_water(east, north):
        raise RuntimeError("B1 is not water in the full-coast mask")
    simulation = run_simulator(
        scene,
        ShipPose(east, north, 0.0),
        seed=pilot["SEED_BASE"] + site_index * 100,
        trial_id="B1-coast-pruning",
        angular_resolution_deg=driver["ANGULAR_RESOLUTION_DEG"],
        map_input=mask,
    )
    start = perf_counter()
    estimate = run_hierarchical_search(
        simulation.measurements,
        catalog,
        coast_bounds,
        HierarchicalSearchConfig(
            coarse_spacing_m=10_000.0,
            refinement_levels=6,
            beam_width=256,
            bearing_sigma_deg=0.5,
            unmatched_penalty_deg=12.0,
            visibility_radius_m=driver["RANGE_M"],
            use_types=True,
            penalize_missing_ais=True,
            angular_resolution_deg=driver["ANGULAR_RESOLUTION_DEG"],
        ),
        visibility=visibility,
    )
    runtime = perf_counter() - start
    # Truth is read only after search returns.
    result = {
        "site": "B1",
        "catalog_records": len(catalog.records),
        "catalog_chart_aids_deduplicated": aid_counts["all_scales_deduplicated"],
        "measurements": len(simulation.measurements.observations),
        "measurement_fingerprint": hashlib.sha256(
            json.dumps(
                [
                    {"type": x.object_type, "bearing": x.bearing_deg, "sigma": x.bearing_sigma_deg}
                    for x in simulation.measurements.observations
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "bounds": coast_bounds,
        "seed": pilot["SEED_BASE"] + site_index * 100,
        "estimate_m": [estimate.east_m, estimate.north_m],
        "error_m": math.hypot(estimate.east_m - east, estimate.north_m - north),
        "score": estimate.score,
        "search_runtime_s": runtime,
        "source_sha256": {
            p: sha(root / p)
            for p in [
                "tools/benchmark_eastern_seaboard_pruning.py",
                "tools/benchmark_eastern_seaboard_search.py",
                "tools/run_ais_enc_pilot.py",
                "bearing_localization/search/hierarchical.py",
                "bearing_localization/search/__init__.py",
            ]
        },
        "input_sha256": {
            p: sha(root / p)
            for p in [
                "data/ais/eastern-seaboard_2023-07-15T1200Z.csv",
                "data/chart-aids/eastern-seaboard-enc-aids.csv",
                "data/maps/eastern-seaboard-water-mask-500m.manifest.json",
                "data/maps/eastern-seaboard-water-mask-500m.npz",
            ]
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
