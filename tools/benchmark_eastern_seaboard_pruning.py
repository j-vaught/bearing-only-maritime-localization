"""Run coast-wide water, ray, and complete-report pruning for B1–B5."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import runpy
from pathlib import Path
from time import perf_counter

import numpy as np

from bearing_localization.scene import AISRecord as SceneAISRecord
from bearing_localization.scene import Landmark, Scene, SceneConfig, ShipPose
from bearing_localization.search import HierarchicalSearchConfig, run_hierarchical_search
from bearing_localization.simulator.runner import run_simulator
from maritime_localization_shared.contracts import AISRecord, AISSnapshot
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.raster_los import raster_line_of_sight
from maritime_localization_shared.geospatial.water_mask import WaterMask

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/eastern-seaboard-pruning"
WATER_PATH = ROOT / "data/maps/eastern-seaboard-water-mask-500m.npz"
RANGE_M = 10_000.0
ANGULAR_RESOLUTION_DEG = 0.1


class CoastVisibility:
    """Map adapter for candidate water checks and raster line-of-sight rays."""

    def __init__(self, mask: WaterMask) -> None:
        self.mask = mask

    def is_water(self, east_m: float, north_m: float) -> bool:
        return self.mask.is_water(east_m, north_m)

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool:
        return raster_line_of_sight(
            self.mask,
            (observer_east_m, observer_north_m),
            (target_east_m, target_north_m),
        )


def main() -> None:
    pilot = runpy.run_path(str(ROOT / "tools/run_ais_enc_pilot.py"))
    benchmark = runpy.run_path(str(ROOT / "tools/benchmark_eastern_seaboard_search.py"))
    _, _, sites, _ = pilot["load_inputs"]()
    _, _, snapshots, coast_bounds, aid_counts = benchmark["build_inputs"]()
    source_catalog: AISSnapshot = snapshots["all_scales_deduplicated"]
    catalog_records = tuple(
        AISRecord(
            record_id=f"catalog:{index}",
            east_m=record.east_m,
            north_m=record.north_m,
            ais_class=record.ais_class,
            source_type_code=record.source_type_code,
        )
        for index, record in enumerate(source_catalog.records)
    )
    catalog = AISSnapshot(
        records=catalog_records,
        timestamp_utc=source_catalog.timestamp_utc,
        crs=source_catalog.crs,
        snapshot_id=source_catalog.snapshot_id,
    )
    packed = np.load(WATER_PATH, allow_pickle=False)
    water_mask = WaterMask(
        packed["water"].astype(bool),
        float(packed["west_m"]),
        float(packed["south_m"]),
        float(packed["cell_size_m"]),
    )
    visibility = CoastVisibility(water_mask)
    catalog_xy = np.asarray([(item.east_m, item.north_m) for item in catalog.records])
    landmarks = tuple(
        Landmark(
            object_id=index,
            east_m=record.east_m,
            north_m=record.north_m,
            object_type=record.ais_class or "unknown",
            has_ais=record.ais_class == "vessel",
        )
        for index, record in enumerate(catalog.records)
    )
    scene_ais = tuple(
        SceneAISRecord(index, item.east_m, item.north_m, item.ais_class or "unknown")
        for index, item in enumerate(catalog.records)
        if item.ais_class == "vessel"
    )
    scene = Scene(
        ais=scene_ais,
        landmarks=landmarks,
        config=SceneConfig(
            width_m=coast_bounds[1] - coast_bounds[0],
            height_m=coast_bounds[3] - coast_bounds[2],
            object_count=len(landmarks),
            ais_fraction=len(scene_ais) / len(landmarks),
            visibility_radius_m=RANGE_M,
            bearing_noise_sigma_deg=0.5,
            report_type_labels=True,
            object_types=("vessel", "aid"),
        ),
    )
    rows: list[dict[str, object]] = []

    for site_index, (site, (east_m, north_m)) in enumerate(sites.items()):
        if not visibility.is_water(east_m, north_m):
            raise RuntimeError(f"{site} is not water in the coast-wide mask.")
        simulation = run_simulator(
            scene,
            ShipPose(east_m, north_m, 0.0),
            seed=pilot["SEED_BASE"] + site_index * 100,
            trial_id=f"{site}-coast-pruning",
            angular_resolution_deg=ANGULAR_RESOLUTION_DEG,
            map_input=water_mask,
        )
        started = perf_counter()
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
                visibility_radius_m=RANGE_M,
                use_types=True,
                penalize_missing_ais=True,
                angular_resolution_deg=ANGULAR_RESOLUTION_DEG,
            ),
            visibility=visibility,
        )
        wall_ms = (perf_counter() - started) * 1000.0
        error_m = math.hypot(estimate.east_m - east_m, estimate.north_m - north_m)

        # Evaluation-only local-window diagnostic. It isolates scoring/refinement
        # from the global candidate-generation problem and is not an estimator result.
        local_bounds = (
            max(coast_bounds[0], east_m - 23_000.0),
            min(coast_bounds[1], east_m + 23_000.0),
            max(coast_bounds[2], north_m - 23_000.0),
            min(coast_bounds[3], north_m + 23_000.0),
        )
        local_estimate = run_hierarchical_search(
            simulation.measurements,
            catalog,
            local_bounds,
            HierarchicalSearchConfig(
                coarse_spacing_m=5_000.0,
                refinement_levels=5,
                beam_width=16,
                bearing_sigma_deg=0.5,
                unmatched_penalty_deg=12.0,
                visibility_radius_m=RANGE_M,
                use_types=True,
                penalize_missing_ais=True,
                angular_resolution_deg=ANGULAR_RESOLUTION_DEG,
            ),
            visibility=visibility,
        )
        local_error_m = math.hypot(local_estimate.east_m - east_m, local_estimate.north_m - north_m)

        # Evaluation-only consistency count at the known pose. It does not enter
        # estimator inputs or candidate scoring.
        offsets = catalog_xy - np.asarray([east_m, north_m])
        in_range = np.flatnonzero(np.einsum("ij,ij->i", offsets, offsets) <= RANGE_M**2)
        visible_indices = [
            int(index)
            for index in in_range
            if visibility.has_line_of_sight(
                east_m,
                north_m,
                float(catalog_xy[index, 0]),
                float(catalog_xy[index, 1]),
            )
        ]
        visible_count = len(visible_indices)
        expected_groups = group_angular_detections(
            [
                DetectedObject(
                    object_id=str(index),
                    object_type=catalog.records[index].ais_class or "unknown",
                    bearing_deg=float(
                        np.degrees(
                            np.arctan2(
                                catalog_xy[index, 0] - east_m,
                                catalog_xy[index, 1] - north_m,
                            )
                        )
                    ),
                    range_m=float(np.hypot(*offsets[index])),
                )
                for index in visible_indices
            ],
            ANGULAR_RESOLUTION_DEG,
        )
        rows.append(
            {
                "site": site,
                "catalog_records": len(catalog.records),
                "simulated_reports": len(simulation.measurements.observations),
                "catalog_objects_in_range_and_los_before_merge": visible_count,
                "expected_catalog_reports_after_merge": len(expected_groups.observations),
                "position_error_m": error_m,
                "oracle_window_error_m_diagnostic_only": local_error_m,
                "success_250m": error_m <= 250.0,
                "search_ms": wall_ms,
                "score": estimate.score,
                "estimate_east_m": estimate.east_m,
                "estimate_north_m": estimate.north_m,
            }
        )
        print(
            f"{site}: detections={len(simulation.measurements.observations)} "
            f"catalog_visible={visible_count} grouped={len(expected_groups.observations)} "
            f"error={error_m:,.0f} m local={local_error_m:,.0f} m "
            f"time={wall_ms:,.1f} ms",
            flush=True,
        )

    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "b1-b5-pruning.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            ROOT / "data/ais/eastern-seaboard_2023-07-15T1200Z.csv",
            ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv",
            ROOT / "data/maps/eastern-seaboard-water-mask-500m.manifest.json",
            WATER_PATH,
        )
    }
    metadata = {
        "catalog_records": len(catalog.records),
        "catalog_chart_aids_deduplicated": aid_counts["all_scales_deduplicated"],
        "ais_timestamp_utc": catalog.timestamp_utc,
        "visibility_range_m": RANGE_M,
        "angular_resolution_deg": ANGULAR_RESOLUTION_DEG,
        "water_mask_cell_size_m": water_mask.cell_size_m,
        "water_mask_source": "Natural Earth 1:10m coastline polygons rasterized at 500 m",
        "search": {
            "algorithm": "water-constrained coarse-to-fine hierarchical search",
            "coarse_spacing_m": 10_000,
            "refinement_levels": 6,
            "beam_width": 256,
            "ray_tracing": "supercover raster traversal through water cells",
            "complete_report_assumption": (
                "Every in-range catalog angular group with clear land LOS generates "
                "a report; extra reports may be unmatched."
            ),
            "catalog_angular_merging": (
                "same 0.1 degree circular rule as simulator; nearest object represents each group"
            ),
        },
        "search_bounds_projected_m": coast_bounds,
        "input_sha256": hashes,
        "rows": rows,
    }
    (OUT / "config.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    readme = """# East Coast pruning experiment

This experiment runs B1–B5 against the full 2023-07-15 12:00 UTC AIS snapshot and
deduplicated NOAA chart-aid catalog. It applies water-only observer candidates,
a 10 km range gate, land-blocked raster rays, and complete-report scoring.
Catalog objects within range and clear line of sight are grouped using the
simulator's 0.1 degree circular angular-resolution rule. A missing predicted
group incurs a penalty; extra detections may remain unmatched.

The water mask uses Natural Earth 1:10m land polygons on a 500 m grid. It is a
coarse experimental land mask, not a navigation chart. B1–B5 measurements are
seeded outputs from the Chesapeake scene. The CSV includes estimator error and
runtime; config.json records settings and source hashes.

Reproduce with `uv run python tools/benchmark_eastern_seaboard_pruning.py`.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
