"""Profile exact assignment share in a complete development search workload.

The input comes from the committed Chesapeake AIS and chart-aid development
catalog around B1. Anonymous reports are formed from nearby development
features; only bearing, sigma, and optional class are passed to search.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from math import floor, radians
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from pyproj import CRS, Geod
from shapely.geometry import box
from shapely.ops import unary_union

from bearing_localization.search import pairwise as pairwise_module
from bearing_localization.search import scoring as scoring_module
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.gpu.assignment import (
    ASSIGNMENT_ACCELERATION_THRESHOLD,
    assignment_acceleration_decision,
)
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.geospatial.raster_los import raster_line_of_sight
from maritime_localization_shared.geospatial.vector_map import VectorMap

ROOT = Path(__file__).resolve().parents[1]
DATA_TIME = "2023-07-15T12:00:00Z"
ORIGIN_LAT_DEG = 38.0
ORIGIN_LON_DEG = -76.2
EARTH_RADIUS_M = 6_371_008.8
LOCAL_CRS = CRS.from_proj4(
    "+proj=eqc +lat_ts=38 +lat_0=38 +lon_0=-76.2 +a=6371008.8 +b=6371008.8 +units=m +no_defs"
)
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_s: float


def run_bounded_process(
    argv: Sequence[str],
    *,
    timeout_s: float,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    terminate_grace_s: float = 2.0,
) -> BoundedProcessResult:
    """Run a subprocess in its own process group and reap the group on timeout."""
    if timeout_s <= 0.0 or terminate_grace_s < 0.0:
        raise ValueError("timeout_s must be positive and terminate_grace_s nonnegative.")
    started = perf_counter()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=terminate_grace_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    return BoundedProcessResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        elapsed_s=perf_counter() - started,
    )


def project(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    east = EARTH_RADIUS_M * radians(lon_deg - ORIGIN_LON_DEG) * np.cos(np.deg2rad(ORIGIN_LAT_DEG))
    north = EARTH_RADIUS_M * radians(lat_deg - ORIGIN_LAT_DEG)
    return float(east), float(north)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _catalog() -> CatalogV2:
    ais_rows = _read(ROOT / "data/ais/chesapeake_2023-07-15T1200Z.csv")
    aid_rows = _read(ROOT / "data/chart-aids/chesapeake-enc-aids.csv")
    records = [
        CatalogRecordV2(
            key=f"ais:{row['object_id']}",
            latitude_deg=float(row["latitude_deg"]),
            longitude_deg=float(row["longitude_deg"]),
            raw_type=row.get("vessel_type_code") or "vessel",
            normalized_type="vessel",
        )
        for row in ais_rows
    ]
    records.extend(
        CatalogRecordV2(
            key=f"enc:{row['aid_id']}",
            latitude_deg=float(row["latitude_deg"]),
            longitude_deg=float(row["longitude_deg"]),
            raw_type=row.get("enc_objl") or "aid",
            normalized_type="aid",
        )
        for row in aid_rows
    )
    return CatalogV2(
        records=tuple(records),
        timestamp_utc=DATA_TIME,
        source="committed-chesapeake-development-catalog",
        catalog_version="2023-07-15",
        catalog_id="a29-b1-profile",
    )


def _development_reports(
    catalog: CatalogV2,
    site_lon: float,
    site_lat: float,
    map_input: MapInput,
    *,
    report_count: int = 5,
) -> MeasurementSetV2:
    candidates: list[tuple[float, str, float, str]] = []
    site_xy = project(site_lon, site_lat)
    for record in catalog.records:
        bearing, _, distance = GEOD.inv(
            site_lon, site_lat, record.longitude_deg, record.latitude_deg
        )
        if not 250.0 <= distance <= 9_500.0:
            continue
        target_xy = project(record.longitude_deg, record.latitude_deg)
        if not raster_line_of_sight(map_input, site_xy, target_xy):
            continue
        candidates.append(
            (
                float(bearing % 360.0),
                record.key,
                float(distance),
                record.normalized_type or "unknown",
            )
        )
    candidates.sort()
    if len(candidates) < report_count:
        raise RuntimeError(
            f"Only {len(candidates)} visible development features are available for reports."
        )
    selected_indices = np.linspace(0, len(candidates) - 1, report_count, dtype=int)
    selected = [candidates[index] for index in selected_indices]
    return MeasurementSetV2(
        measurements=tuple(
            MeasurementV2(bearing, DATA_TIME, 0.5, object_type)
            for bearing, _, _, object_type in selected
        ),
        trial_id="development-b1-assignment-profile",
    )


def _vector_map(site_lon: float, site_lat: float) -> tuple[VectorMap, MapInput]:
    packed = np.load(ROOT / "data/maps/chesapeake-water-mask-500m.npz")
    water = tuple(tuple(bool(cell) for cell in row) for row in packed["water"])
    map_input = MapInput(
        crs=str(packed["crs"]),
        west_m=float(packed["west_m"]),
        south_m=float(packed["south_m"]),
        cell_size_m=float(packed["cell_size_m"]),
        water_mask=water,
    )
    center_x, center_y = project(site_lon, site_lat)
    coverage_radius_m = 50_000.0
    coverage = box(
        center_x - coverage_radius_m,
        center_y - coverage_radius_m,
        center_x + coverage_radius_m,
        center_y + coverage_radius_m,
    )
    cell = map_input.cell_size_m
    min_col = max(0, floor((center_x - coverage_radius_m - map_input.west_m) / cell))
    max_col = min(
        len(water[0]),
        floor((center_x + coverage_radius_m - map_input.west_m) / cell) + 1,
    )
    min_row = max(0, floor((center_y - coverage_radius_m - map_input.south_m) / cell))
    max_row = min(
        len(water),
        floor((center_y + coverage_radius_m - map_input.south_m) / cell) + 1,
    )
    land_cells = [
        box(
            map_input.west_m + col * cell,
            map_input.south_m + row * cell,
            map_input.west_m + (col + 1) * cell,
            map_input.south_m + (row + 1) * cell,
        )
        for row in range(min_row, max_row)
        for col in range(min_col, max_col)
        if not water[row][col]
    ]
    if not land_cells:
        raise RuntimeError("The B1 development map window contains no land cells.")
    land = unary_union(land_cells)
    return VectorMap(land, crs=LOCAL_CRS, coverage_geometry=coverage), map_input


def _timed_call(
    original: Callable[..., Any], stage: str, timings: dict[str, float]
) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            timings[stage] += perf_counter() - started

    return wrapped


def _timed_generator(original: Callable[..., Any], timings: dict[str, float]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any):
        stream = iter(original(*args, **kwargs))
        while True:
            started = perf_counter()
            try:
                item = next(stream)
            except StopIteration:
                timings["proposal_generation"] += perf_counter() - started
                return
            timings["proposal_generation"] += perf_counter() - started
            yield item

    return wrapped


def _run_one(
    measurements: MeasurementSetV2,
    catalog: CatalogV2,
    vector_map: VectorMap,
    config: SearchConfig,
) -> tuple[dict[str, float], dict[str, Any], int]:
    timings = {
        "proposal_generation": 0.0,
        "visible_group_geometry": 0.0,
        "likelihood_cost_generation": 0.0,
        "exact_assignment": 0.0,
    }
    originals = (
        scoring_module.predict_visible_groups,
        scoring_module.build_likelihood_costs,
        scoring_module.assign_likelihood_costs,
        pairwise_module.generate_geographic_pairwise_proposals,
    )
    for module, name, wrapped in (
        (
            scoring_module,
            "predict_visible_groups",
            _timed_call(originals[0], "visible_group_geometry", timings),
        ),
        (
            scoring_module,
            "build_likelihood_costs",
            _timed_call(originals[1], "likelihood_cost_generation", timings),
        ),
        (
            scoring_module,
            "assign_likelihood_costs",
            _timed_call(originals[2], "exact_assignment", timings),
        ),
        (
            pairwise_module,
            "generate_geographic_pairwise_proposals",
            _timed_generator(originals[3], timings),
        ),
    ):
        setattr(module, name, wrapped)
    try:
        scorer = scoring_module.CandidateScorer(measurements, catalog, vector_map, config)
        started = perf_counter()
        run = pairwise_module.run_pairwise_search_v2(
            scorer, batch_size=32, max_proposals=config.candidate_budget
        )
        search_total_s = perf_counter() - started
        localization = run.localization
        result_payload = {
            "decision_status": localization.decision_status,
            "search_status": localization.search_status,
            "certificate_scope": localization.certificate_scope,
            "candidate_count": run.candidate_count,
            "scored_hypothesis_count": len(localization.hypotheses),
            "best_score": (localization.hypotheses[0].score if localization.hypotheses else None),
            "elapsed_s": localization.elapsed_s,
            "outcome_counts": dict(run.outcome_counts),
        }
        complete_output = {
            "localization": asdict(localization),
            "candidate_count": run.candidate_count,
            "outcome_counts": dict(run.outcome_counts),
        }
        started = perf_counter()
        serialized = json.dumps(complete_output, allow_nan=False, separators=(",", ":"))
        serialization_s = perf_counter() - started
        result_payload["serialized_bytes"] = len(serialized.encode("utf-8"))
        stages_sum = sum(timings.values())
        timings["search_other"] = max(search_total_s - stages_sum, 0.0)
        timings["search_total"] = search_total_s
        timings["decision_serialization"] = serialization_s
        timings["complete_workload"] = search_total_s + serialization_s
        return timings, result_payload, len(serialized)
    finally:
        (
            scoring_module.predict_visible_groups,
            scoring_module.build_likelihood_costs,
            scoring_module.assign_likelihood_costs,
            pairwise_module.generate_geographic_pairwise_proposals,
        ) = originals


def profile(candidate_budget: int = 128, repeats: int = 3) -> dict[str, Any]:
    if candidate_budget < 1 or repeats < 1:
        raise ValueError("candidate_budget and repeats must be positive.")
    sites = _read(ROOT / "data/sites/b1-b5.csv")
    site = next(row for row in sites if row["site_id"] == "B1")
    site_lon, site_lat = float(site["longitude_deg"]), float(site["latitude_deg"])
    catalog = _catalog()
    vector_map, map_input = _vector_map(site_lon, site_lat)
    measurements = _development_reports(catalog, site_lon, site_lat, map_input)
    center_x, center_y = project(site_lon, site_lat)
    config = SearchConfig(
        algorithm="pairwise",
        bounds_xy=(
            center_x - 2_000.0,
            center_x + 2_000.0,
            center_y - 2_000.0,
            center_y + 2_000.0,
        ),
        max_range_m=10_000.0,
        candidate_budget=candidate_budget,
        use_types=True,
    )
    _run_one(measurements, catalog, vector_map, config)
    samples: list[dict[str, Any]] = []
    for _ in range(repeats):
        timings, result, serialized_length = _run_one(measurements, catalog, vector_map, config)
        samples.append(
            {
                "stage_times_s": timings,
                "result": result,
                "serialized_bytes": serialized_length,
            }
        )
    median_times = {
        name: statistics.median(sample["stage_times_s"][name] for sample in samples)
        for name in samples[0]["stage_times_s"]
    }
    denominator = median_times["complete_workload"]
    assignment_fraction = median_times["exact_assignment"] / denominator if denominator else 0.0
    decision = assignment_acceleration_decision(assignment_fraction)
    return {
        "experiment": "a29-development-assignment-profile",
        "status": "defer" if decision == "defer_cpu" else "prototype_eligible",
        "assignment_decision": decision,
        "threshold_fraction": ASSIGNMENT_ACCELERATION_THRESHOLD,
        "assignment_fraction_of_complete_workload": assignment_fraction,
        "objective_version": config.objective_version,
        "backend": "cpu",
        "workload": {
            "split": "development",
            "scene": "Chesapeake B1",
            "data": [
                "data/ais/chesapeake_2023-07-15T1200Z.csv",
                "data/chart-aids/chesapeake-enc-aids.csv",
                "data/maps/chesapeake-water-mask-500m.npz",
                "data/sites/b1-b5.csv",
            ],
            "catalog_records": len(catalog.records),
            "measurement_count": len(measurements.measurements),
            "candidate_budget": candidate_budget,
            "output_frame_bounds_half_width_m": 2_000.0,
            "max_range_m": config.max_range_m,
            "repeats": repeats,
            "warmup_runs": 1,
        },
        "timing_medians_s": median_times,
        "samples": samples,
        "external_gpu_activity": {
            "observed_before_cpu_profile": True,
            "device_zero": "external process occupied about 24.6 GiB; no GPU workload was run",
            "gpu_timing_claim": False,
        },
        "notes": [
            "Search receives anonymous bearings and optional classes only.",
            "The workload includes geodesic proposals, water and visibility geometry, likelihood, "
            "exact assignment, final decision, and JSON serialization.",
            "Catalog, map, measurements, and spatial index construction are prepared before "
            "the timed search workload.",
            "The CPU exact solver remains the only assignment implementation; "
            "no HyLAC prototype was built.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-budget", type=int, default=128)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results/accuracy/assignment-profile-a29.json"
    )
    args = parser.parse_args()
    result = profile(args.candidate_budget, args.repeats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "assignment_fraction_of_complete_workload",
                    "timing_medians_s",
                    "workload",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
