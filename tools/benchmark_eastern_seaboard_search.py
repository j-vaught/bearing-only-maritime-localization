"""Compare bearing-search methods on the full East Coast candidate catalog.

This is an offline B1 diagnostic. Measurements come from the seeded Chesapeake
simulator case, while search sees the coast-wide July 2023 AIS snapshot and the
current NOAA chart-aid catalog. Search receives no truth or land mask.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import runpy
import time
from dataclasses import replace
from pathlib import Path

from bearing_localization.evaluation.metrics import evaluate_estimate
from bearing_localization.search import (
    HierarchicalSearchConfig,
    PairwiseSearchConfig,
    RandomizedSearchConfig,
    run_hierarchical_search,
    run_pairwise_search,
)
from bearing_localization.search.grid import GridSearchConfig, run_grid_search
from bearing_localization.search.randomized import randomized_search
from maritime_localization_shared.contracts import AISRecord, AISSnapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/eastern-seaboard-search"
AIS_PATH = ROOT / "data/ais/eastern-seaboard_2023-07-15T1200Z.csv"
AIDS_PATH = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv"
R_EARTH_M = 6_371_008.8
VISIBILITY_RANGE_M = 10_000.0
BAND_PRIORITY = {
    "overview": 1,
    "general": 2,
    "coastal": 3,
    "approach": 4,
    "harbour": 5,
    "berthing": 6,
}


def project(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    east = R_EARTH_M * math.radians(lon_deg + 76.2) * math.cos(math.radians(38.0))
    north = R_EARTH_M * math.radians(lat_deg - 38.0)
    return east, north


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def coast_aid_rows(scale_band: str | None = None) -> list[dict[str, str]]:
    raw_rows = read_csv(AIDS_PATH)
    selected: dict[tuple[str, float, float], dict[str, str]] = {}
    for row in raw_rows:
        if scale_band is not None and row["scale_band"] != scale_band:
            continue
        layer_class = row["service_layer"].split(".", 1)[-1]
        if layer_class.endswith("_point"):
            layer_class = layer_class[: -len("_point")]
        lon, lat = float(row["longitude"]), float(row["latitude"])
        key = (layer_class, round(lon, 5), round(lat, 5))
        existing = selected.get(key)
        if (
            existing is None
            or BAND_PRIORITY[row["scale_band"]] > BAND_PRIORITY[existing["scale_band"]]
        ):
            selected[key] = row
    return list(selected.values())


def build_inputs():
    pilot = runpy.run_path(str(ROOT / "tools/run_ais_enc_pilot.py"))
    scene, water_map, sites, _ = pilot["load_inputs"]()
    simulation = pilot["run_simulator"](
        scene,
        pilot["ShipPose"](*sites["B1"], 0.0),
        seed=pilot["SEED_BASE"],
        trial_id="B1-full-coast-search-diagnostic",
        angular_resolution_deg=pilot["ANGULAR_RESOLUTION_DEG"],
        map_input=water_map,
    )

    ais_rows = read_csv(AIS_PATH)
    local_ais_rows = read_csv(ROOT / "data/ais/chesapeake_2023-07-15T1200Z.csv")
    local_ids_by_coordinate = {
        (
            round(float(row["longitude_deg"]), 5),
            round(float(row["latitude_deg"]), 5),
        ): index
        for index, row in enumerate(local_ais_rows)
    }
    vessel_records: list[AISRecord] = []
    used_local_ids: set[int] = set()
    for row in ais_rows:
        lon, lat = float(row["longitude_deg"]), float(row["latitude_deg"])
        east, north = project(lon, lat)
        local_id = local_ids_by_coordinate.get((round(lon, 5), round(lat, 5)))
        if local_id in used_local_ids:
            local_id = None
        if local_id is not None:
            used_local_ids.add(local_id)
        record_id = f"ais:{local_id}" if local_id is not None else f"coast-ais:{row['object_id']}"
        vessel_records.append(
            AISRecord(
                record_id=record_id,
                east_m=east,
                north_m=north,
                ais_class="vessel",
                source_type_code=row["vessel_type_code"],
            )
        )

    def snapshot_for_aids(aids: list[dict[str, str]], snapshot_id: str) -> AISSnapshot:
        records = list(vessel_records)
        for index, row in enumerate(aids):
            east, north = project(float(row["longitude"]), float(row["latitude"]))
            records.append(
                AISRecord(
                    record_id=f"{snapshot_id}:enc-aid:{index}",
                    east_m=east,
                    north_m=north,
                    ais_class="aid",
                    source_type_code=row.get("objl") or row["service_layer"],
                )
            )
        return AISSnapshot(
            records=tuple(records),
            timestamp_utc="2023-07-15T12:00:00Z",
            crs="local-equirectangular-origin-38N-76.2W",
            snapshot_id=snapshot_id,
        )

    all_aids = coast_aid_rows()
    approach_aids = coast_aid_rows(scale_band="approach")

    west, south, east_edge, north_edge = -82.0, 24.5, -66.0, 45.5
    xmin, ymin = project(west, south)
    xmax, ymax = project(east_edge, north_edge)
    snapshots = {
        "all_scales_deduplicated": snapshot_for_aids(
            all_aids, "eastern-seaboard-ais-and-all-scale-aids"
        ),
        "approach_band": snapshot_for_aids(approach_aids, "eastern-seaboard-ais-and-approach-aids"),
    }
    counts = {"all_scales_deduplicated": len(all_aids), "approach_band": len(approach_aids)}
    return simulation.measurements, simulation.truth, snapshots, (xmin, xmax, ymin, ymax), counts


def methods(measurements, ais: AISSnapshot, bounds, use_types: bool):
    yield (
        "grid_100km",
        lambda: run_grid_search(
            measurements,
            ais,
            bounds,
            GridSearchConfig(
                grid_spacing_m=100_000.0,
                visibility_radius_m=VISIBILITY_RANGE_M,
                use_types=use_types,
                refine=True,
                refine_starts=16,
            ),
        ),
    )
    yield (
        "hierarchical_100km",
        lambda: run_hierarchical_search(
            measurements,
            ais,
            bounds,
            HierarchicalSearchConfig(
                coarse_spacing_m=100_000.0,
                refinement_levels=4,
                beam_width=8,
                visibility_radius_m=VISIBILITY_RANGE_M,
                use_types=use_types,
            ),
        ),
    )
    yield (
        "randomized_2000",
        lambda: randomized_search(
            measurements,
            ais,
            RandomizedSearchConfig(
                bounds_xy=bounds,
                visibility_radius_m=VISIBILITY_RANGE_M,
                use_types=use_types,
                iterations=2_000,
                seed=20_262_024,
            ),
        ),
    )
    yield (
        "pairwise_pruned",
        lambda: run_pairwise_search(
            measurements,
            ais,
            bounds,
            PairwiseSearchConfig(
                visibility_radius_m=VISIBILITY_RANGE_M,
                use_types=use_types,
                max_hypotheses=5_000,
                max_visibility_hypotheses=128,
                refine_starts=16,
                max_pair_sources=500,
                max_neighbors_per_source=32,
            ),
        ),
    )
    yield (
        "hierarchical_two_stage",
        lambda: hierarchical_two_stage(measurements, ais, bounds, use_types),
    )


def hierarchical_two_stage(measurements, ais: AISSnapshot, bounds, use_types: bool):
    """Run a global hierarchy, prune to a local catalog, then refine locally."""
    started = time.perf_counter()
    coarse = run_hierarchical_search(
        measurements,
        ais,
        bounds,
        HierarchicalSearchConfig(
            coarse_spacing_m=100_000.0,
            refinement_levels=4,
            beam_width=8,
            visibility_radius_m=VISIBILITY_RANGE_M,
            use_types=use_types,
        ),
    )
    local_radius_m = 50_000.0
    local_records = tuple(
        record
        for record in ais.records
        if math.hypot(record.east_m - coarse.east_m, record.north_m - coarse.north_m)
        <= local_radius_m + VISIBILITY_RANGE_M
    )
    xmin = max(bounds[0], coarse.east_m - local_radius_m)
    xmax = min(bounds[1], coarse.east_m + local_radius_m)
    ymin = max(bounds[2], coarse.north_m - local_radius_m)
    ymax = min(bounds[3], coarse.north_m + local_radius_m)
    result = coarse
    if len(local_records) >= 2 and xmin < xmax and ymin < ymax:
        local_catalog = AISSnapshot(
            records=local_records,
            timestamp_utc=ais.timestamp_utc,
            crs=ais.crs,
            snapshot_id=f"{ais.snapshot_id}-local-refinement",
        )
        result = run_hierarchical_search(
            measurements,
            local_catalog,
            (xmin, xmax, ymin, ymax),
            HierarchicalSearchConfig(
                coarse_spacing_m=5_000.0,
                refinement_levels=5,
                beam_width=16,
                visibility_radius_m=VISIBILITY_RANGE_M,
                use_types=use_types,
            ),
        )
    return replace(
        result,
        algorithm="hierarchical_two_stage",
        elapsed_s=time.perf_counter() - started,
    )


def main() -> None:
    measurements, truth, snapshots, bounds, aid_counts = build_inputs()
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for catalog_name, ais in snapshots.items():
        for use_types in (True, False):
            for algorithm, run in methods(measurements, ais, bounds, use_types):
                started = time.perf_counter()
                estimate = run()
                elapsed_ms = (time.perf_counter() - started) * 1_000.0
                metrics = evaluate_estimate(estimate, truth)
                rows.append(
                    {
                        "chart_aid_catalog": catalog_name,
                        "algorithm": algorithm,
                        "type_labels_used": use_types,
                        "candidate_count": len(ais.records),
                        "ais_vessel_count": sum(item.ais_class == "vessel" for item in ais.records),
                        "enc_aid_count_deduplicated": aid_counts[catalog_name],
                        "observation_count": len(measurements.observations),
                        "position_error_m": metrics.position_error_m,
                        "correct_ais_associations": metrics.correct_associations,
                        "success_250m_and_3_matches": metrics.success,
                        "elapsed_ms": elapsed_ms,
                        "objective_score": estimate.score,
                        "matched_count": len(estimate.matched_pairs),
                    }
                )
                print(
                    f"{catalog_name:26s} {algorithm:20s} types={use_types!s:5s} "
                    f"error={metrics.position_error_m:,.1f} m "
                    f"matches={metrics.correct_associations} "
                    f"time={elapsed_ms:,.1f} ms",
                    flush=True,
                )

    csv_path = OUT / "algorithm-comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    input_hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (AIS_PATH, AIDS_PATH)
    }
    metadata = {
        "region_bbox_wgs84": [-82.0, 24.5, -66.0, 45.5],
        "search_bounds_projected_m": bounds,
        "ais_timestamp_utc": "2023-07-15T12:00:00Z",
        "chart_aid_source": "NOAA ENC Direct to GIS, retrieved 2026-10-24",
        "visibility_range_m": VISIBILITY_RANGE_M,
        "simulated_case": (
            "seeded B1 Chesapeake report evaluated against the complete coast-wide catalog"
        ),
        "land_water_mask_used_for_search": False,
        "land_line_of_sight_used_for_search": False,
        "bearing_geometry": (
            "local equirectangular projection centered near Chesapeake; "
            "coast-wide timing diagnostic"
        ),
        "chart_aid_deduplication": (
            "service layer class plus coordinates rounded to five decimal degrees; "
            "prefer largest available ENC scale band"
        ),
        "pairwise_pruning": {
            "target_pair_radius_m": 2 * VISIBILITY_RANGE_M,
            "max_hypotheses": 5_000,
            "max_pair_sources": 500,
            "max_neighbors_per_source": 32,
        },
        "success_rule": "position error <= 250 m and at least 3 correct AIS associations",
        "input_sha256": input_hashes,
        "rows": rows,
    }
    (OUT / "config.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
