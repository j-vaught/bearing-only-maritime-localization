"""Evaluator-only audit of omitted geodesic-wedge cells for one frozen trial."""

from __future__ import annotations

import argparse
import hashlib
import json
from math import asin, ceil, degrees
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Geod, Transformer

from bearing_localization.search.config_serialization import read_search_config
from bearing_localization.search.proposals.geodesic_wedge import _maximum_distinct_matching
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.contracts.truth import EvaluationTruth
from maritime_localization_shared.geospatial.coordinates import LocalFrame
from maritime_localization_shared.geospatial.reverse_bearing import solve_reverse_bearing_observer
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

BASE = Path(
    "/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a26-wedge-geodesic-diagnostic"
)
TRIAL = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
GEOD = Geod(ellps="WGS84")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _witness_cell(
    i: int,
    j: int,
    lon: float,
    lat: float,
    observations: tuple[Any, ...],
    keys: list[str],
    source_lon: np.ndarray,
    source_lat: np.ndarray,
    true_keys_by_report: dict[int, set[str]],
    sigmas: list[float],
    *,
    cell_size_m: float,
    max_range_m: float,
    angular_tolerance_deg: float,
) -> dict[str, Any]:
    """Vectorized exact cell-center acceptance predicate from geodesic_wedge."""
    half = cell_size_m * 2.0**0.5 / 2.0
    by_report: dict[int, set[str]] = {}
    detail: dict[str, Any] = {}
    norm_by_report: list[float] = []
    evidence_by_report: list[float] = []
    for report, obs in enumerate(observations):
        bearings, _, ranges = GEOD.inv(
            np.full(source_lon.shape, lon), np.full(source_lat.shape, lat), source_lon, source_lat
        )
        bearings, ranges = (
            np.asarray(bearings, dtype=np.float64),
            np.asarray(ranges, dtype=np.float64),
        )
        finite = np.isfinite(ranges)
        near = finite & (ranges <= half)
        far = finite & (ranges > half) & (ranges <= max_range_m + half)
        quant = np.zeros_like(ranges)
        quant[far] = np.degrees(np.arcsin(np.minimum(1.0, half / ranges[far])))
        residual = np.abs((bearings - obs.bearing_deg + 180.0) % 360.0 - 180.0)
        accepted = near | (far & (residual <= angular_tolerance_deg + quant))
        indices = np.flatnonzero(accepted)
        report = int(report)
        by_report[report] = {keys[k] for k in indices}
        truth_hits = by_report[report] & true_keys_by_report.get(report, set())
        norm = np.ones_like(ranges)
        regular = far & accepted
        norm[regular] = np.minimum(
            1.0, residual[regular] / np.maximum(sigmas[report] + quant[regular], 1e-12)
        )
        precision = np.minimum(1.0, sigmas[report] / np.maximum(sigmas[report] + quant, 1e-12))
        norm_value = float(np.min(norm[indices])) if indices.size else None
        evidence_value = (
            float((precision * (1.0 - norm))[indices[int(np.argmin(norm[indices]))]])
            if indices.size
            else None
        )
        if norm_value is not None and evidence_value is not None:
            norm_by_report.append(float(norm_value))
            evidence_by_report.append(float(evidence_value))
        detail[str(report)] = {
            "witness_count": int(indices.size),
            "true_group_member_hits": len(truth_hits),
            "other_witness_count": int(indices.size) - len(truth_hits),
            "minimum_absolute_bearing_residual_deg": float(np.min(residual[indices]))
            if indices.size
            else None,
            "minimum_normalized_residual": norm_value,
            "maximum_source_evidence": evidence_value,
        }
    return {
        "candidate_id": f"cell:{i}:{j}",
        "cell_i": i,
        "cell_j": j,
        "vote_count": len(by_report),
        "matching_count": _maximum_distinct_matching({r: sorted(v) for r, v in by_report.items()}),
        "unique_witness_count": len(set().union(*by_report.values())),
        "witness_keys_by_report": {str(r): sorted(v) for r, v in by_report.items()},
        "mean_normalized_angular_residual": float(np.mean(norm_by_report))
        if norm_by_report
        else None,
        "evidence_score": float(np.sum(evidence_by_report)),
        "by_report": detail,
    }


def _sample_splat_check(
    cell_i: int,
    cell_j: int,
    witness_keys_by_report: dict[str, list[str]],
    catalog_by_key: dict[str, Any],
    observations: tuple[Any, ...],
    frame: LocalFrame,
    *,
    cell_size_m: float,
    max_range_m: float,
    tolerance_deg: float,
    solver_tolerance_deg: float,
) -> dict[str, Any]:
    """Replay only predicate-eligible source pairs through exact ray sampling and splat."""
    half_diagonal = cell_size_m * 2.0**0.5 / 2.0
    final_distance_m = max_range_m + half_diagonal
    step_m = cell_size_m / 2.0
    sample_count = max(1, int(ceil(final_distance_m / step_m)) + 1)
    sample_distances = np.arange(sample_count, dtype=np.float64) * step_m
    sample_distances[-1] = final_distance_m
    angular_gap_rad = 2.0 * asin(
        min(1.0, cell_size_m / (4.0 * max(max_range_m, cell_size_m / 2.0)))
    )
    angular_gap_deg = degrees(angular_gap_rad)
    angular_count = max(1, int(ceil(2.0 * tolerance_deg / angular_gap_deg)))
    angular_offsets = np.linspace(
        -tolerance_deg, tolerance_deg, angular_count + 1, dtype=np.float64
    )
    if not np.any(np.isclose(angular_offsets, 0.0, atol=1e-12)):
        angular_offsets = np.sort(np.concatenate((angular_offsets, np.asarray([0.0]))))
    forward = Transformer.from_crs("EPSG:4326", frame.projected_crs, always_xy=True)
    origin_x, origin_y = forward.transform(*frame.origin_lonlat)
    rows = []
    for report_text, keys in sorted(witness_keys_by_report.items(), key=lambda item: int(item[0])):
        report = int(report_text)
        sources = np.asarray(
            [(catalog_by_key[key].longitude_deg, catalog_by_key[key].latitude_deg) for key in keys],
            dtype=np.float64,
        )
        desired = (observations[report].bearing_deg + angular_offsets[None, :, None]) % 360.0
        solved = solve_reverse_bearing_observer(
            sources[:, None, None, :],
            desired,
            sample_distances[None, None, :],
            tolerance_deg=solver_tolerance_deg,
        )
        points = np.asarray(solved.observer_lonlat_deg, dtype=np.float64)
        valid = np.all(np.isfinite(points), axis=-1) & (
            np.asarray(solved.converged) | np.asarray(solved.degenerate)
        )
        x, y = forward.transform(points[..., 0], points[..., 1])
        base_i = np.floor((np.asarray(x) - origin_x) / cell_size_m).astype(np.int64)
        base_j = np.floor((np.asarray(y) - origin_y) / cell_size_m).astype(np.int64)
        splat = valid & (np.abs(base_i - cell_i) <= 1) & (np.abs(base_j - cell_j) <= 1)
        rows.append(
            {
                "report_index": report,
                "predicate_eligible_source_pairs": len(keys),
                "source_pairs_with_neighbor_splat_to_cell": int(
                    np.count_nonzero(np.any(splat, axis=(1, 2)))
                ),
                "valid_sample_neighbor_hits": int(np.count_nonzero(splat)),
            }
        )
    return {
        "cell_i": cell_i,
        "cell_j": cell_j,
        "angular_offset_count": len(angular_offsets),
        "range_sample_count": len(sample_distances),
        "per_report": rows,
        "eligible_source_pair_count": sum(row["predicate_eligible_source_pairs"] for row in rows),
        "source_pair_count_with_neighbor_splat": sum(
            row["source_pairs_with_neighbor_splat_to_cell"] for row in rows
        ),
    }


def diagnose(output_path: Path) -> dict[str, Any]:
    static = BASE / "static-inputs"
    run = BASE / "run"
    truth_dir = BASE / "evaluator-only/truth"
    measurements = read_contract(static / "measurements.json", MeasurementSetV2)
    catalog = read_contract(static / "catalog.json", CatalogV2)
    map_input = read_contract(static / "map.json", MapV2)
    config = read_search_config(run / "search-config.json")
    truth = read_contract(truth_dir / "truth.json", EvaluationTruth)
    crs_meta = json.loads((truth_dir / "truth-crs.json").read_text(encoding="utf-8"))
    truth_crs = crs_meta["coordinate_reference_system"]
    if truth.trial_id != TRIAL:
        raise ValueError("Evaluator truth is not the frozen development trial.")
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    truth_lonlat = Transformer.from_crs(truth_crs, "EPSG:4326", always_xy=True).transform(
        truth.observer_east_m, truth.observer_north_m
    )
    if config.bounds_xy is None:
        raise ValueError("Frozen geodesic search config requires bounds.")
    xmin, xmax, ymin, ymax = config.bounds_xy
    origin = Transformer.from_crs(vector_map.crs, "EPSG:4326", always_xy=True).transform(
        (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    )
    frame = LocalFrame(origin)
    truth_local = frame.to_local(truth_lonlat)
    ci, cj = int(np.floor(truth_local[0] / 250.0)), int(np.floor(truth_local[1] / 250.0))
    to_map = Transformer.from_crs(frame.projected_crs, vector_map.crs, always_xy=True)
    from_map = Transformer.from_crs(vector_map.crs, "EPSG:4326", always_xy=True)
    true_keys_by_report: dict[int, set[str]] = {}
    for obj in truth.objects:
        if obj.observation_index is not None:
            key = str(obj.ais_record_id).removeprefix("catalog:")
            true_keys_by_report.setdefault(int(obj.observation_index), set()).add(key)
    keys = [record.key for record in catalog.records]
    catalog_by_key = {record.key: record for record in catalog.records}
    slon = np.asarray([record.longitude_deg for record in catalog.records], dtype=np.float64)
    slat = np.asarray([record.latitude_deg for record in catalog.records], dtype=np.float64)
    sigmas = [
        max(
            o.sigma_deg if o.sigma_deg is not None else config.generated_sigma_deg,
            config.sigma_floor_deg,
        )
        for o in measurements.measurements
    ]
    rows = []
    witness_rows = []
    for i in range(ci - 1, ci + 2):
        for j in range(cj - 1, cj + 2):
            xy = to_map.transform((i + 0.5) * 250.0, (j + 0.5) * 250.0)
            ev = scorer.evaluate((float(xy[0]), float(xy[1])))
            h = ev.hypothesis
            matches = []
            if h is not None and ev.predicted is not None:
                for a in h.assignments:
                    group = next(
                        (g for g in ev.predicted.groups if g.representative_key == a.catalog_key),
                        None,
                    )
                    members = set(group.member_keys) if group else {a.catalog_key}
                    truth_members = true_keys_by_report.get(a.report_index, set())
                    matches.append(
                        {
                            "report_index": a.report_index,
                            "catalog_key": a.catalog_key,
                            "residual_deg": a.residual_deg,
                            "cost": a.cost,
                            "truth_group_members": sorted(truth_members),
                            "predicted_group_members": sorted(members),
                            "exact_group_match": members == truth_members,
                        }
                    )
            components = h.score_components if h is not None else None
            rows.append(
                {
                    "candidate_id": f"cell:{i}:{j}",
                    "cell_i": i,
                    "cell_j": j,
                    "position_epsg32618": list(xy),
                    "water_status": vector_map.point_water_status(tuple(xy)),
                    "status": ev.status,
                    "score": h.score if h else None,
                    "score_components": (
                        {
                            "matched_cost": components.matched_cost,
                            "clutter_cost": components.clutter_cost,
                            "missed_cost": components.missed_cost,
                            "total_cost": components.total_cost,
                        }
                        if components is not None
                        else None
                    ),
                    "matched_count": len(h.assignments) if h else 0,
                    "predicted_group_count": len(ev.predicted.groups) if ev.predicted else None,
                    "matches": matches,
                }
            )
            lon, lat = from_map.transform(*xy)
            witness_rows.append(
                _witness_cell(
                    i,
                    j,
                    lon,
                    lat,
                    measurements.measurements,
                    keys,
                    slon,
                    slat,
                    true_keys_by_report,
                    sigmas,
                    cell_size_m=250.0,
                    max_range_m=config.max_range_m,
                    angular_tolerance_deg=0.5,
                )
            )
    containing_witness = next(
        row for row in witness_rows if row["candidate_id"] == f"cell:{ci}:{cj}"
    )
    containing_cell_splat = _sample_splat_check(
        ci,
        cj,
        containing_witness["witness_keys_by_report"],
        catalog_by_key,
        measurements.measurements,
        frame,
        cell_size_m=250.0,
        max_range_m=config.max_range_m,
        tolerance_deg=0.5,
        solver_tolerance_deg=config.proposal_solver_tolerance_deg,
    )
    truth_xy_raw = Transformer.from_crs(truth_crs, vector_map.crs, always_xy=True).transform(
        truth.observer_east_m, truth.observer_north_m
    )
    truth_xy = (float(truth_xy_raw[0]), float(truth_xy_raw[1]))
    tev = scorer.evaluate(truth_xy)
    telemetry = json.loads((run / "estimate.telemetry.json").read_text())
    diagnostics = telemetry["diagnostics"]
    evaluated = diagnostics["exact_scoring"]["evaluated_proposal_witnesses"]
    result = {
        "schema": "a35-wedge-omission-diagnostic-v1",
        "trial_id": TRIAL,
        "input_sha256": {
            p.name: sha(p)
            for p in [
                static / "measurements.json",
                static / "map.json",
                static / "catalog.json",
                static / "scope-manifest.json",
                run / "search-config.json",
                run / "estimate.json",
                run / "estimate.telemetry.json",
                truth_dir / "truth.json",
                truth_dir / "truth-crs.json",
            ]
        },
        "truth": {
            "crs": truth_crs,
            "lonlat": list(truth_lonlat),
            "local_aeqd_xy": list(truth_local),
            "containing_cell": [ci, cj],
            "water_status": vector_map.point_water_status(truth_xy),
            "score": tev.hypothesis.score if tev.hypothesis else None,
            "score_components": (
                {
                    "matched_cost": tev.hypothesis.score_components.matched_cost,
                    "clutter_cost": tev.hypothesis.score_components.clutter_cost,
                    "missed_cost": tev.hypothesis.score_components.missed_cost,
                    "total_cost": tev.hypothesis.score_components.total_cost,
                }
                if tev.hypothesis is not None and tev.hypothesis.score_components is not None
                else None
            ),
            "matched_count": len(tev.hypothesis.assignments) if tev.hypothesis else 0,
        },
        "frame": {
            "origin_lonlat": list(origin),
            "crs": str(frame.projected_crs),
            "cell_size_m": 250.0,
            "half_diagonal_m": 250.0 * 2.0**0.5 / 2.0,
            "range_limit_m": config.max_range_m + 250.0 * 2.0**0.5 / 2.0,
        },
        "cells": rows,
        "exact_generator_cell_predicate": {
            "catalog_count": len(catalog.records),
            "source_pair_count": len(catalog.records) * len(measurements.measurements),
            "angular_tolerance_deg": 0.5,
            "matching_vote_histogram": diagnostics["proposal_generation"][
                "matching_vote_histogram"
            ],
            "cell_witnesses": witness_rows,
            "containing_cell_sample_splat": containing_cell_splat,
            "evaluated_witness_count": len(evaluated),
            "evaluated_final_witness": evaluated[-1],
            "containing_cell_evaluated": f"cell:{ci}:{cj}"
            in {item["candidate_id"] for item in evaluated},
        },
        "diagnostic_scope": "post-search fixed-point audit; no truth-derived search points",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "truth_score": result["truth"]["score"],
                "cell_scores": [r["score"] for r in result["cells"]],
                "containing_cell": result["truth"]["containing_cell"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
