"""Reproduce the seeded B1–B5 search comparison from committed AIS/ENC inputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from bearing_localization.evaluation.metrics import evaluate_estimate
from bearing_localization.scene import AISRecord as LegacyAISRecord
from bearing_localization.scene import Landmark, Scene, SceneConfig, ShipPose
from bearing_localization.search import (
    HierarchicalSearchConfig,
    PairwiseSearchConfig,
    run_hierarchical_search,
    run_pairwise_search,
)
from bearing_localization.simulator.runner import run_simulator
from maritime_localization_shared.contracts import AISRecord, AISSnapshot, MapInput
from maritime_localization_shared.geospatial.raster_los import raster_line_of_sight

ROOT = Path(__file__).resolve().parents[1]
R_EARTH_M = 6_371_008.8
ORIGIN_LAT_DEG = 38.0
ORIGIN_LON_DEG = -76.2
BOUNDS_LON_LAT = (-76.8, -75.5, 36.5, 39.5)
VISIBILITY_RANGE_M = 10_000.0
BEARING_SIGMA_DEG = 0.5
ANGULAR_RESOLUTION_DEG = 0.1
SEED_BASE = 20_262_023


def project(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    east = (
        R_EARTH_M * math.radians(lon_deg - ORIGIN_LON_DEG) * math.cos(math.radians(ORIGIN_LAT_DEG))
    )
    north = R_EARTH_M * math.radians(lat_deg - ORIGIN_LAT_DEG)
    return east, north


class RasterVisibility:
    """Water/land adapter backed by the versioned 500 m raster."""

    def __init__(self, map_input: MapInput) -> None:
        self.map_input = map_input

    def is_water(self, east_m: float, north_m: float) -> bool:
        col = math.floor((east_m - self.map_input.west_m) / self.map_input.cell_size_m)
        row = math.floor((north_m - self.map_input.south_m) / self.map_input.cell_size_m)
        return (
            0 <= row < self.map_input.height_cells
            and 0 <= col < self.map_input.width_cells
            and self.map_input.water_mask[row][col]
        )

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool:
        return raster_line_of_sight(
            self.map_input,
            (observer_east_m, observer_north_m),
            (target_east_m, target_north_m),
        )


def load_inputs() -> tuple[
    Scene,
    MapInput,
    dict[str, tuple[float, float]],
    tuple[float, float, float, float],
]:
    with (ROOT / "data/ais/chesapeake_2023-07-15T1200Z.csv").open(newline="") as stream:
        ais_rows = list(csv.DictReader(stream))
    with (ROOT / "data/chart-aids/chesapeake-enc-aids.csv").open(newline="") as stream:
        aid_rows = list(csv.DictReader(stream))
    with (ROOT / "data/sites/b1-b5.csv").open(newline="") as stream:
        site_rows = list(csv.DictReader(stream))

    ais: list[LegacyAISRecord] = []
    landmarks: list[Landmark] = []
    for index, row in enumerate(ais_rows):
        east, north = project(float(row["longitude_deg"]), float(row["latitude_deg"]))
        ais.append(LegacyAISRecord(index, east, north, "vessel"))
        landmarks.append(Landmark(index, east, north, "vessel", True))
    for index, row in enumerate(aid_rows):
        east, north = project(float(row["longitude_deg"]), float(row["latitude_deg"]))
        object_id = len(ais_rows) + index
        landmarks.append(Landmark(object_id, east, north, "aid", False))

    x_min, y_min = project(BOUNDS_LON_LAT[0], BOUNDS_LON_LAT[2])
    x_max, y_max = project(BOUNDS_LON_LAT[1], BOUNDS_LON_LAT[3])
    scene = Scene(
        ais=tuple(ais),
        landmarks=tuple(landmarks),
        config=SceneConfig(
            width_m=x_max - x_min,
            height_m=y_max - y_min,
            object_count=len(landmarks),
            ais_fraction=len(ais) / len(landmarks),
            visibility_radius_m=VISIBILITY_RANGE_M,
            bearing_noise_sigma_deg=BEARING_SIGMA_DEG,
            report_type_labels=True,
            object_types=("vessel", "aid"),
        ),
    )

    packed = np.load(ROOT / "data/maps/chesapeake-water-mask-500m.npz")
    water = tuple(tuple(bool(cell) for cell in row) for row in packed["water"])
    map_input = MapInput(
        crs=str(packed["crs"]),
        west_m=float(packed["west_m"]),
        south_m=float(packed["south_m"]),
        cell_size_m=float(packed["cell_size_m"]),
        water_mask=water,
    )
    sites = {
        row["site_id"]: project(float(row["longitude_deg"]), float(row["latitude_deg"]))
        for row in site_rows
    }
    bounds: tuple[float, float, float, float] = (x_min, x_max, y_min, y_max)
    return scene, map_input, sites, bounds


def candidate_snapshot(run, aid_landmarks: tuple[Landmark, ...]) -> AISSnapshot:
    """Expose AIS and ENC coordinates as separate typed search candidates."""
    records = list(run.ais_snapshot.records)
    # AIS records are created in the simulator from the legacy scene records.
    # Chart aids have no AIS identifier; their separate prefix keeps that
    # distinction visible in search output and evaluation.
    if not aid_landmarks:
        raise RuntimeError("The ENC aid candidate catalog is empty.")
    for index, item in enumerate(aid_landmarks):
        records.append(AISRecord(f"aid:{index}", item.east_m, item.north_m, "aid", "ENC"))
    return AISSnapshot(
        records=tuple(records),
        timestamp_utc=run.ais_snapshot.timestamp_utc,
        crs=run.ais_snapshot.crs,
        snapshot_id=run.ais_snapshot.snapshot_id,
    )


def metrics_row(
    estimate, run, site: str, replicate: int, seed: int, method: str
) -> dict[str, object]:
    ais_only = replace(
        estimate,
        matched_pairs=tuple(pair for pair in estimate.matched_pairs if pair[1].startswith("ais:")),
    )
    score = evaluate_estimate(ais_only, run.truth)
    true_aid_pairs = {
        (obj.observation_index, f"aid:{index}")
        for index, obj in enumerate(item for item in run.truth.objects if item.object_type == "aid")
        if obj.observation_index is not None
    }
    predicted_aids = {pair for pair in estimate.matched_pairs if pair[1].startswith("aid:")}
    return {
        "site": site,
        "replicate": replicate,
        "seed": seed,
        "method": method,
        "detections": len(run.measurements.observations),
        "visible_ais_bearings": sum(
            obj.ais_record_id is not None and obj.observation_index is not None
            for obj in run.truth.objects
        ),
        "visible_aid_reports": sum(
            obj.object_type == "aid" and obj.observation_index is not None
            for obj in run.truth.objects
        ),
        "position_error_m": score.position_error_m,
        "correct_ais_associations": score.correct_associations,
        "matched_chart_aids": len(true_aid_pairs & predicted_aids),
        "false_ais_associations": score.false_associations,
        "success_250m_3_ais": score.success,
        "search_time_ms": estimate.elapsed_s * 1000.0,
        "estimate_east_m": estimate.east_m,
        "estimate_north_m": estimate.north_m,
    }


def run(replicates: int, output: Path) -> list[dict[str, object]]:
    scene, map_input, sites, bounds = load_inputs()
    visibility = RasterVisibility(map_input)
    rows: list[dict[str, object]] = []
    for site_index, (site, (east, north)) in enumerate(sites.items()):
        if not visibility.is_water(east, north):
            raise RuntimeError(f"{site} observer is not on a water cell in the committed mask.")
        for replicate in range(replicates):
            seed = SEED_BASE + site_index * 100 + replicate
            trial = replace(
                scene, config=replace(scene.config, visibility_radius_m=VISIBILITY_RANGE_M)
            )
            simulated = run_simulator(
                trial,
                ShipPose(east, north, 0.0),
                seed=seed,
                trial_id=f"{site}-r{replicate + 1}",
                angular_resolution_deg=ANGULAR_RESOLUTION_DEG,
                map_input=map_input,
            )
            known_aid_catalog = tuple(item for item in trial.landmarks if not item.has_ais)
            search_catalog = candidate_snapshot(simulated, known_aid_catalog)
            experiments = (
                (
                    "hierarchical_1km",
                    lambda: run_hierarchical_search(
                        simulated.measurements,
                        search_catalog,
                        bounds,
                        HierarchicalSearchConfig(
                            coarse_spacing_m=1_000.0,
                            refinement_levels=4,
                            beam_width=8,
                            visibility_radius_m=VISIBILITY_RANGE_M,
                            use_types=True,
                            penalize_missing_ais=True,
                        ),
                        visibility=visibility,
                    ),
                ),
                (
                    "pairwise_hypothesis",
                    lambda: run_pairwise_search(
                        simulated.measurements,
                        search_catalog,
                        bounds,
                        PairwiseSearchConfig(
                            visibility_radius_m=VISIBILITY_RANGE_M,
                            use_types=True,
                            penalize_missing_ais=True,
                            max_hypotheses=20_000,
                        ),
                        visibility=visibility,
                    ),
                ),
            )
            for method, search in experiments:
                started = perf_counter()
                try:
                    estimate = search()
                    row = metrics_row(estimate, simulated, site, replicate + 1, seed, method)
                except (ValueError, RuntimeError) as exc:
                    row: dict[str, object] = {
                        "site": site,
                        "replicate": replicate + 1,
                        "seed": seed,
                        "method": method,
                        "detections": len(simulated.measurements.observations),
                        "visible_ais_bearings": sum(
                            obj.ais_record_id is not None and obj.observation_index is not None
                            for obj in simulated.truth.objects
                        ),
                        "visible_aid_reports": sum(
                            obj.object_type == "aid" and obj.observation_index is not None
                            for obj in simulated.truth.objects
                        ),
                        "position_error_m": "",
                        "correct_ais_associations": 0,
                        "matched_chart_aids": 0,
                        "false_ais_associations": 0,
                        "success_250m_3_ais": False,
                        "search_time_ms": (perf_counter() - started) * 1000.0,
                        "estimate_east_m": "",
                        "estimate_north_m": "",
                        "status": f"failed: {exc}",
                    }
                rows.append(row)
                print(json.dumps(row, sort_keys=True), flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as stream:
        fieldnames = list(rows[0])
        fieldnames.extend(key for row in rows for key in row if key not in fieldnames)
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_summaries(rows, output.parent)
    return rows


def write_summaries(rows: list[dict[str, object]], output_dir: Path) -> None:
    """Write per-site and overall accuracy/latency summaries."""

    def as_float(value: object) -> float:
        if not isinstance(value, (int, float, str)):
            raise TypeError(f"Expected a numeric value, received {type(value).__name__}.")
        return float(value)

    def grouped(group_keys: tuple[str, ...]) -> list[dict[str, object]]:
        groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
        for row in rows:
            key = tuple(row[item] for item in group_keys)
            groups.setdefault(key, []).append(row)
        summary_rows: list[dict[str, object]] = []
        for key, group in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
            valid = [row for row in group if row["position_error_m"] not in ("", None)]
            errors = [as_float(row["position_error_m"]) for row in valid]
            times = [as_float(row["search_time_ms"]) for row in group]
            successes = sum(
                row["success_250m_3_ais"] is True
                or str(row["success_250m_3_ais"]).casefold() == "true"
                for row in group
            )
            output_row: dict[str, object] = dict(zip(group_keys, key, strict=True))
            output_row.update(
                {
                    "trials": len(group),
                    "valid_estimates": len(valid),
                    "successes": successes,
                    "success_rate": successes / len(group),
                    "median_position_error_m": median(errors) if errors else "",
                    "p95_position_error_m": float(np.percentile(errors, 95)) if errors else "",
                    "median_search_ms": median(times),
                    "p95_search_ms": float(np.percentile(times, 95)),
                    "median_valid_estimate_search_ms": (
                        median(as_float(row["search_time_ms"]) for row in valid) if valid else ""
                    ),
                    "p95_valid_estimate_search_ms": (
                        float(np.percentile([as_float(row["search_time_ms"]) for row in valid], 95))
                        if valid
                        else ""
                    ),
                    "median_correct_ais_matches": median(
                        as_float(row["correct_ais_associations"]) for row in group
                    ),
                    "median_chart_aid_matches": median(
                        as_float(row["matched_chart_aids"]) for row in group
                    ),
                }
            )
            summary_rows.append(output_row)
        return summary_rows

    for filename, keys in (
        ("summary.csv", ("site", "method")),
        ("overall-summary.csv", ("method",)),
    ):
        summary_rows = grouped(keys)
        with (output_dir / filename).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
            writer.writeheader()
            writer.writerows(summary_rows)

    # Compare methods only on the same site/seed cases where both returned estimates.
    paired: dict[tuple[str, int], dict[str, dict[str, object]]] = {}
    for row in rows:
        if row["position_error_m"] in ("", None):
            continue
        paired.setdefault((str(row["site"]), int(as_float(row["replicate"]))), {})[
            str(row["method"])
        ] = row
    matched = [cases for cases in paired.values() if len(cases) == 2]
    matched_summary: list[dict[str, object]] = []
    for method in sorted({name for case in matched for name in case}):
        values = [case[method] for case in matched]
        errors = [as_float(row["position_error_m"]) for row in values]
        times = [as_float(row["search_time_ms"]) for row in values]
        matched_summary.append(
            {
                "method": method,
                "matched_cases": len(values),
                "successes": sum(
                    row["success_250m_3_ais"] is True
                    or str(row["success_250m_3_ais"]).casefold() == "true"
                    for row in values
                ),
                "median_position_error_m": median(errors),
                "median_search_ms": median(times),
            }
        )
    with (output_dir / "matched-case-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(matched_summary[0]))
        writer.writeheader()
        writer.writerows(matched_summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results/ais-b1-b5-pilot-v2-pairwise/trials.csv",
    )
    args = parser.parse_args()
    if args.replicates < 1:
        parser.error("--replicates must be positive")
    run(args.replicates, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
