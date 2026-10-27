"""GPU-sampled bearing/object-pair RANSAC for a bounded geographic tile."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import cupy as cp
import numpy as np
from pyproj import Geod, Proj, Transformer
from scipy.spatial import cKDTree  # ty: ignore[unresolved-import]

from maritime.accuracy_cli import SearchV2Session
from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.search.config_serialization import read_search_config
from maritime.search.gpu.experimental_pipeline import score_candidate_positions_experimental
from maritime.search.gpu.search_adapter import (
    GpuCandidateScorer,
    score_candidate_positions_batched,
)
from maritime.search.progress import SearchProgressReporter
from maritime.search.scoring import CandidateSearchRun
from maritime.search.telemetry import SearchTelemetry
from maritime.serialization import read_contract, write_contract

_GEOD = Geod(ellps="WGS84")


def run_ransac(
    measurements: MeasurementSetV2,
    scorer: GpuCandidateScorer,
    *,
    iterations: int,
    seed: int,
    batch_size: int,
    cell_size_m: float,
    progress: SearchProgressReporter,
    consensus_score_limit: int,
    experimental_batched_pipeline: bool = False,
) -> tuple[CandidateSearchRun, dict[str, object]]:
    """Sample compatible-in-range anchor pairs, then score consensus cells."""
    config = scorer.config
    bounds = config.bounds_xy
    if bounds is None:
        raise ValueError("RANSAC requires finite search bounds.")
    xmin, xmax, ymin, ymax = bounds
    records = scorer.catalog.records
    to_map = Transformer.from_crs("EPSG:4326", scorer.vector_map.crs, always_xy=True)
    east, north = to_map.transform(
        [item.longitude_deg for item in records], [item.latitude_deg for item in records]
    )
    coordinates = np.column_stack((east, north)).astype(np.float64, copy=False)
    object_lon = np.asarray([item.longitude_deg for item in records])
    object_lat = np.asarray([item.latitude_deg for item in records])
    factors = Proj(scorer.vector_map.crs).get_factors(object_lon, object_lat)
    projection_radius_scale = max(1.0, float(np.max(factors.meridional_scale)) * 1.01)
    projected_max_range = config.max_range_m * projection_radius_scale
    object_pairs = cKDTree(coordinates).query_pairs(
        2.0 * projected_max_range, output_type="ndarray"
    )
    if len(object_pairs) == 0:
        raise ValueError("No catalog object pairs lie within twice the sensor range.")

    report_bearings = np.asarray([item.bearing_deg for item in measurements.measurements])
    report_count = len(report_bearings)
    report_pairs = np.column_stack(np.triu_indices(report_count, k=1)).astype(np.int64)
    # Projection convergence varies along a coast-spanning search.  Compute the
    # local direction of true north at each anchor rather than using the center.
    north_lon, north_lat, _ = _GEOD.fwd(
        object_lon, object_lat, np.zeros(len(records)), np.full(len(records), 1_000.0)
    )
    north_e, north_n = to_map.transform(north_lon, north_lat)
    grid_correction = np.arctan2(
        np.asarray(north_e) - coordinates[:, 0], np.asarray(north_n) - coordinates[:, 1]
    )
    report_angles = np.deg2rad(report_bearings)
    directions = np.column_stack((np.sin(report_angles), np.cos(report_angles)))
    pair_determinants = (
        directions[report_pairs[:, 0], 1] * directions[report_pairs[:, 1], 0]
        - directions[report_pairs[:, 0], 0] * directions[report_pairs[:, 1], 1]
    )
    report_pairs = report_pairs[np.abs(pair_determinants) >= np.sin(np.deg2rad(0.1))]
    if len(report_pairs) == 0:
        raise ValueError("No bearing pairs have sufficient crossing angle.")

    nx = int(np.ceil((xmax - xmin) / cell_size_m))
    ny = int(np.ceil((ymax - ymin) / cell_size_m))
    report_presence = np.zeros((ny * nx, report_count), dtype=bool)
    proposal_counts = np.zeros(ny * nx, dtype=np.int64)
    sum_east = np.zeros(ny * nx, dtype=np.float64)
    sum_north = np.zeros(ny * nx, dtype=np.float64)
    rng = np.random.default_rng(seed)
    valid_total = 0
    in_bounds_total = 0

    with cp.cuda.Device(scorer.device_ids[0]):
        cp_report_angles = cp.asarray(report_angles)
        cp_grid_correction = cp.asarray(grid_correction)
        cp_coords = cp.asarray(coordinates)
        cp_object_pairs = cp.asarray(object_pairs)
        cp_report_pairs = cp.asarray(report_pairs)
        for start in range(0, iterations, batch_size):
            count = min(batch_size, iterations - start)
            report_selection = rng.integers(0, len(report_pairs), size=count)
            object_selection = rng.integers(0, len(object_pairs), size=count)
            swap = rng.integers(0, 2, size=count, dtype=np.int8).astype(bool)
            cp_report_selection = cp.asarray(report_selection)
            cp_object_selection = cp.asarray(object_selection)
            cp_swap = cp.asarray(swap)
            report_i = cp_report_pairs[cp_report_selection, 0]
            report_j = cp_report_pairs[cp_report_selection, 1]
            object_i = cp_object_pairs[cp_object_selection, 0]
            object_j = cp_object_pairs[cp_object_selection, 1]
            source_index = cp.where(cp_swap, object_j, object_i)
            target_index = cp.where(cp_swap, object_i, object_j)
            a = cp_coords[source_index]
            b = cp_coords[target_index]
            angle_i = cp_report_angles[report_i] + cp_grid_correction[source_index]
            angle_j = cp_report_angles[report_j] + cp_grid_correction[target_index]
            u = cp.stack((cp.sin(angle_i), cp.cos(angle_i)), axis=1)
            v = cp.stack((cp.sin(angle_j), cp.cos(angle_j)), axis=1)
            delta = a - b
            determinant = u[:, 1] * v[:, 0] - u[:, 0] * v[:, 1]
            range_i = (-delta[:, 0] * v[:, 1] + delta[:, 1] * v[:, 0]) / determinant
            range_j = (u[:, 0] * delta[:, 1] - u[:, 1] * delta[:, 0]) / determinant
            x = a[:, 0] - range_i * u[:, 0]
            y = a[:, 1] - range_i * u[:, 1]
            valid = (
                (cp.abs(determinant) >= np.sin(np.deg2rad(0.1)))
                & (range_i > 0.0)
                & (range_j > 0.0)
                & (range_i <= projected_max_range)
                & (range_j <= projected_max_range)
                & (x >= xmin)
                & (x <= xmax)
                & (y >= ymin)
                & (y <= ymax)
            )
            selected = cp.asnumpy(cp.nonzero(valid)[0])
            valid_total += int(selected.size)
            if selected.size:
                points = cp.asnumpy(cp.stack((x[selected], y[selected]), axis=1))
                ri = cp.asnumpy(report_i[selected]).astype(np.int64, copy=False)
                rj = cp.asnumpy(report_j[selected]).astype(np.int64, copy=False)
                ix = np.minimum(nx - 1, np.floor((points[:, 0] - xmin) / cell_size_m).astype(int))
                iy = np.minimum(ny - 1, np.floor((points[:, 1] - ymin) / cell_size_m).astype(int))
                flat = iy * nx + ix
                np.add.at(proposal_counts, flat, 1)
                np.add.at(sum_east, flat, points[:, 0])
                np.add.at(sum_north, flat, points[:, 1])
                report_presence[flat, ri] = True
                report_presence[flat, rj] = True
                in_bounds_total += int(selected.size)
            progress("gpu_pair_hypotheses", min(start + count, iterations), iterations)

    neighborhood_reports = np.zeros_like(report_presence)
    neighborhood_counts = np.zeros_like(proposal_counts)
    neighborhood_east = np.zeros_like(sum_east)
    neighborhood_north = np.zeros_like(sum_north)
    grid = np.arange(ny * nx).reshape(ny, nx)
    report_presence_grid = report_presence.reshape(ny, nx, report_count)
    neighborhood_reports_grid = neighborhood_reports.reshape(ny, nx, report_count)
    for dy in (-1, 0, 1):
        y_src = slice(max(0, -dy), min(ny, ny - dy))
        y_dst = slice(max(0, dy), min(ny, ny + dy))
        for dx in (-1, 0, 1):
            x_src = slice(max(0, -dx), min(nx, nx - dx))
            x_dst = slice(max(0, dx), min(nx, nx + dx))
            source_grid = grid[y_src, x_src].ravel()
            target_grid = grid[y_dst, x_dst].ravel()
            neighborhood_reports_grid[y_dst, x_dst] |= report_presence_grid[y_src, x_src]
            neighborhood_counts[target_grid] += proposal_counts[source_grid]
            neighborhood_east[target_grid] += sum_east[source_grid]
            neighborhood_north[target_grid] += sum_north[source_grid]
    support = neighborhood_reports.sum(axis=1)
    ranked = np.argsort(support)[::-1]
    candidates: list[tuple[float, float]] = []
    consensus: list[int] = []
    seen: set[tuple[int, int]] = set()
    for flat in ranked:
        if support[flat] < 3 or neighborhood_counts[flat] == 0:
            break
        iy, ix = divmod(int(flat), nx)
        if any((iy + dy, ix + dx) in seen for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
            continue
        seen.add((iy, ix))
        candidates.append(
            (
                float(neighborhood_east[flat] / neighborhood_counts[flat]),
                float(neighborhood_north[flat] / neighborhood_counts[flat]),
            )
        )
        consensus.append(int(support[flat]))
        if len(candidates) >= config.candidate_budget:
            break
    if not candidates:
        raise RuntimeError("RANSAC produced no candidate cluster with three-report support.")

    candidates = candidates[:consensus_score_limit]
    consensus = consensus[:consensus_score_limit]
    if experimental_batched_pipeline:
        pipeline = score_candidate_positions_experimental(
            scorer,
            candidates,
            batch_size=8,
            progress=progress,
            telemetry=scorer.telemetry,
            device_id=scorer.device_ids[0],
        )
        scoring = pipeline.run
        pipeline_diagnostics: dict[str, object] = {
            "experimental_batched_pipeline": 1,
            "pipeline_shoreline_cpu_fallbacks": pipeline.shoreline_cpu_fallbacks,
            "pipeline_scored_candidates": dict(scoring.outcome_counts).get("scored", 0),
            "pipeline_cpu_verified_costs_only": 1,
            "pipeline_full_gpu": 0,
            "pipeline_candidate_ids": list(pipeline.stable_candidate_ids),
            "pipeline_candidate_outcomes": [
                {"candidate_id": candidate_id, "status": status}
                for candidate_id, status in pipeline.candidate_outcomes
            ],
        }
        pipeline_diagnostics.update(
            {f"pipeline_{name}_s": value for name, value in pipeline.stage_seconds.items()}
        )
    else:
        scoring = score_candidate_positions_batched(
            scorer,
            candidates,
            batch_size=8,
            candidate_limit=min(config.candidate_budget, len(candidates)),
            progress_callback=lambda completed, total: progress(
                "gpu_consensus_scoring", completed, total
            ),
            progress_total=max(1, min(config.candidate_budget, len(candidates))),
        )
        pipeline_diagnostics = {"experimental_batched_pipeline": 0}
    result = replace(
        scoring.localization,
        budget_limit=iterations,
        budget_used=iterations,
        certificate_scope="none",
    )
    diagnostics: dict[str, object] = {
        "proposal_samples": iterations,
        "catalog_neighbor_pairs": int(len(object_pairs)),
        "bearing_pairs": int(len(report_pairs)),
        "valid_range_hypotheses": valid_total,
        "bounded_hypotheses": in_bounds_total,
        "consensus_candidates": len(candidates),
        "top_report_support": max(consensus, default=0),
        "cell_size_m": cell_size_m,
        "projection_radius_scale": projection_radius_scale,
        **pipeline_diagnostics,
    }
    return CandidateSearchRun(
        localization=result,
        candidate_count=scoring.candidate_count,
        outcome_counts=scoring.outcome_counts,
    ), diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=10_000_000)
    parser.add_argument("--batch-size", type=int, default=250_000)
    parser.add_argument("--cell-size-m", type=float, default=500.0)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--progress-interval-s", type=float, default=5.0)
    parser.add_argument("--consensus-score-limit", type=int, default=128)
    parser.add_argument("--experimental-batched-pipeline", action="store_true")
    args = parser.parse_args()
    if (
        args.iterations < 1
        or args.batch_size < 1
        or args.cell_size_m <= 0.0
        or args.consensus_score_limit < 1
    ):
        parser.error("iterations, batch size, and cell size must be positive")
    started = perf_counter()
    session = SearchV2Session(args.catalog, args.map, args.projected_crs)
    measurements = read_contract(args.measurements, MeasurementSetV2)
    config = read_search_config(args.config)
    telemetry = SearchTelemetry(
        objective_version=config.objective_version,
        backend="gpu",
        device_ids=(args.device_id,),
    )
    scorer = GpuCandidateScorer(
        measurements,
        session._snapshot.catalog,
        session._snapshot.vector_map,
        config,
        device_ids=(args.device_id,),
        telemetry=telemetry,
    )
    progress = SearchProgressReporter(args.progress_interval_s)
    with telemetry.stage("gpu_pair_ransac"):
        run, diagnostics = run_ransac(
            measurements,
            scorer,
            iterations=args.iterations,
            seed=args.seed,
            batch_size=args.batch_size,
            cell_size_m=args.cell_size_m,
            progress=progress,
            consensus_score_limit=args.consensus_score_limit,
            experimental_batched_pipeline=args.experimental_batched_pipeline,
        )
    write_contract(args.output, run.localization)
    args.output.with_suffix(args.output.suffix + ".diagnostics.json").write_text(
        json.dumps(
            {**diagnostics, "elapsed_wall_s": perf_counter() - started},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    telemetry_path = args.output.with_suffix(args.output.suffix + ".telemetry.json")
    telemetry_path.write_text(json.dumps(telemetry.as_dict(), sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                "decision_status": run.localization.decision_status,
                "search_status": run.localization.search_status,
                "hypotheses": len(run.localization.hypotheses),
                "candidate_count": run.candidate_count,
                **diagnostics,
                "elapsed_wall_s": perf_counter() - started,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
