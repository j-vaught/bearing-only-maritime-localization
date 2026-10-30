"""Compare CuPy batched prediction and scoring with CPU references on saved candidates.

The visibility matrix is generated with the CPU VectorMap line-of-sight routine.
This isolates batched prediction and scoring from the experimental GPU shoreline.
"""

from __future__ import annotations

import argparse
import json
from math import atan2, degrees, hypot
from pathlib import Path
from time import perf_counter
from typing import Any

import cupy as cp
import numpy as np
from pyproj import Geod, Transformer

from maritime.accuracy_cli import SearchV2Session
from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.search.config_serialization import read_search_config
from maritime.search.gpu.batch_prediction import predict_candidates_cupy
from maritime.search.gpu.batch_scoring import score_prediction_batch_cupy
from maritime.search.scoring import CandidateScorer
from maritime.serialization import read_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("measurements", "catalog", "map", "config", "candidates", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--device-id", type=int, default=0)
    args = parser.parse_args()
    if args.max_candidates < 1:
        parser.error("max-candidates must be positive")

    start = perf_counter()
    with cp.cuda.Device(args.device_id):
        session = SearchV2Session(args.catalog, args.map, args.projected_crs)
        measurements = read_contract(args.measurements, MeasurementSetV2)
        config = read_search_config(args.config)
        catalog = session._snapshot.catalog
        vector_map = session._snapshot.vector_map
        scorer = CandidateScorer(measurements, catalog, vector_map, config)
        source = json.loads(args.candidates.read_text())
        hypotheses = source.get("hypotheses", [])
        if not hypotheses:
            raise ValueError("Candidate input contains no hypotheses.")
        selected_indices = np.unique(
            np.linspace(
                0, len(hypotheses) - 1, min(args.max_candidates, len(hypotheses)), dtype=int
            )
        )
        positions = [
            (float(hypotheses[int(i)]["east_m"]), float(hypotheses[int(i)]["north_m"]))
            for i in selected_indices
        ]
        candidate_ids = tuple(int(i) for i in selected_indices)
        cpu_results = [scorer.evaluate(position) for position in positions]
        context = scorer._prediction_context
        records = [record for record in context.records if record.is_physical]

        geod = Geod(ellps="WGS84")
        target_lonlat: list[tuple[float, float]] = []
        target_xy: list[tuple[float, float]] = []
        for prepared in records:
            record = prepared.record
            lon, lat = record.longitude_deg, record.latitude_deg
            if prepared.offset_quality == "calibrated" and (
                prepared.east_offset_m or prepared.north_offset_m
            ):
                azimuth = degrees(atan2(prepared.east_offset_m, prepared.north_offset_m)) % 360.0
                lon, lat, _ = geod.fwd(
                    lon,
                    lat,
                    azimuth,
                    hypot(prepared.east_offset_m, prepared.north_offset_m),
                )
            target_lonlat.append((float(lon), float(lat)))
            target_east, target_north = context.forward.transform(lon, lat)
            target_xy.append((float(target_east), float(target_north)))

        inverse = Transformer.from_crs(vector_map.crs, "EPSG:4326", always_xy=True)
        candidates_lonlat = np.asarray(
            [inverse.transform(east, north) for east, north in positions], dtype=np.float64
        )
        keys = tuple(prepared.record.key for prepared in records)
        options: dict[str, Any] = {
            "candidate_ids": candidate_ids,
            "object_keys": keys,
            "object_types": [
                prepared.record.normalized_type or prepared.record.raw_type for prepared in records
            ],
            "visibility_radius_m": config.max_range_m,
            "minimum_range_m": config.min_range_m,
            "range_inclusive": config.range_inclusive,
            "angular_resolution_deg": config.circular_merge_tolerance_deg,
            "type_labels_enabled": config.use_types,
            "angular_half_width_deg": [prepared.half_width_deg for prepared in records],
            "catalog_uncertainty_m": [
                prepared.uncertainty_m if prepared.uncertainty_m is not None else np.nan
                for prepared in records
            ],
            "unsupported_keys": [
                prepared.record.key
                for prepared in records
                if prepared.offset_quality == "unsupported"
            ],
            "identity_limited_keys": [
                prepared.record.key for prepared in records if prepared.identity_limited
            ],
            "offset_quality": [prepared.offset_quality for prepared in records],
        }
        targets_xy = np.asarray(target_xy, dtype=np.float64)
        target_lonlat_array = np.asarray(target_lonlat, dtype=np.float64)

        # First compute pair geometry on CuPy. CPU LOS is evaluated only for pairs
        # that the same configured range interval admits.
        preliminary = predict_candidates_cupy(
            candidates_lonlat,
            target_lonlat_array,
            visibility_state=np.zeros((len(positions), len(records)), dtype=np.uint8),
            **options,
        )
        in_range = cp.asnumpy(preliminary.in_range)
        visibility = np.zeros(in_range.shape, dtype=np.uint8)
        observer_status = []
        for index, position in enumerate(positions):
            observer_status.append(vector_map.point_water_status(position))
            selected_targets = targets_xy[in_range[index]]
            if len(selected_targets):
                states = vector_map.line_visibility_many(position, selected_targets)
                visibility[index, in_range[index]] = [
                    {"blocked": 0, "clear": 1, "unknown": 2}[state] for state in states
                ]

        prediction = predict_candidates_cupy(
            candidates_lonlat,
            target_lonlat_array,
            visibility_state=visibility,
            observer_status=observer_status,
            **options,
        )
        _, assignment = score_prediction_batch_cupy(measurements, list(prediction.groups), config)
        cp.cuda.get_current_stream().synchronize()

        rows = []
        for index, (position, cpu_result, gpu_prediction) in enumerate(
            zip(positions, cpu_results, prediction.groups, strict=True)
        ):
            cpu_prediction = cpu_result.predicted
            entry: dict[str, Any] = {
                "hypothesis_index": candidate_ids[index],
                "east_m": position[0],
                "north_m": position[1],
                "cpu_status": cpu_result.status,
                "cpu_group_count": len(cpu_prediction.groups) if cpu_prediction else None,
                "gpu_group_count": len(gpu_prediction.groups),
                "cpu_coverage": cpu_prediction.coverage_status if cpu_prediction else None,
                "gpu_coverage": gpu_prediction.coverage_status,
                "cpu_unresolved_model": (
                    cpu_prediction.unresolved_model if cpu_prediction else None
                ),
                "gpu_unresolved_model": gpu_prediction.unresolved_model,
                "cpu_identity_limited_count": (
                    len(cpu_prediction.identity_limited_keys) if cpu_prediction else None
                ),
                "gpu_identity_limited_count": len(gpu_prediction.identity_limited_keys),
                "in_range_pairs": int(in_range[index].sum()),
                "cpu_los_visible_pairs": int((visibility[index] == 1).sum()),
                "cpu_los_blocked_pairs": int((visibility[index] == 0).sum()),
                "cpu_los_unknown_pairs": int((visibility[index] == 2).sum()),
                "group_membership_match": False,
            }
            if cpu_prediction is not None:
                entry.update(_compare_groups(cpu_prediction, gpu_prediction))
            if cpu_result.hypothesis is not None:
                entry.update(
                    cpu_score=cpu_result.hypothesis.score,
                    gpu_score=float(assignment.device_costs[index]),
                    score_error=float(assignment.device_costs[index]) - cpu_result.hypothesis.score,
                    cpu_matches=len(cpu_result.hypothesis.assignments),
                    gpu_matches=len(assignment.matches[index]),
                )
            rows.append(entry)

        report = {
            "visibility_source": "CPU VectorMap.line_visibility_many",
            "gpu_shoreline_used": False,
            "candidate_count": len(positions),
            "catalog_record_count": len(catalog.records),
            "catalog_physical_record_count": len(records),
            "offset_quality_counts": _quality_counts(records),
            "identity_limited_catalog_count": sum(record.identity_limited for record in records),
            "elapsed_wall_s": perf_counter() - start,
            "results": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, sort_keys=True))
    return 0


def _compare_groups(cpu: Any, gpu: Any) -> dict[str, Any]:
    cpu_groups = {group.member_keys: group for group in cpu.groups}
    gpu_groups = {group.member_keys: group for group in gpu.groups}
    shared = cpu_groups.keys() & gpu_groups.keys()
    range_errors = [abs(cpu_groups[key].range_m - gpu_groups[key].range_m) for key in shared]
    bearing_errors = [
        abs((cpu_groups[key].bearing_deg - gpu_groups[key].bearing_deg + 180.0) % 360.0 - 180.0)
        for key in shared
    ]
    extent_errors = [
        abs(cpu_groups[key].extent_half_width_deg - gpu_groups[key].extent_half_width_deg)
        for key in shared
    ]
    return {
        "group_membership_match": cpu_groups.keys() == gpu_groups.keys(),
        "cpu_only_groups": [list(keys) for keys in sorted(cpu_groups.keys() - gpu_groups.keys())],
        "gpu_only_groups": [list(keys) for keys in sorted(gpu_groups.keys() - cpu_groups.keys())],
        "shared_group_count": len(shared),
        "type_mismatch_count": sum(
            cpu_groups[key].object_type != gpu_groups[key].object_type for key in shared
        ),
        "representative_mismatch_count": sum(
            cpu_groups[key].representative_key != gpu_groups[key].representative_key
            for key in shared
        ),
        "uncertainty_mismatch_count": sum(
            cpu_groups[key].catalog_uncertainty_m != gpu_groups[key].catalog_uncertainty_m
            for key in shared
        ),
        "offset_quality_mismatch_count": sum(
            cpu_groups[key].visible_feature_offset_quality
            != gpu_groups[key].visible_feature_offset_quality
            for key in shared
        ),
        "max_abs_range_error_m": max(range_errors, default=0.0),
        "max_wrapped_bearing_error_deg": max(bearing_errors, default=0.0),
        "max_extent_error_deg": max(extent_errors, default=0.0),
        "identity_limited_keys_match": set(cpu.identity_limited_keys)
        == set(gpu.identity_limited_keys),
        "unresolved_member_keys_match": cpu.unresolved_member_keys == gpu.unresolved_member_keys,
        "unresolved_model_keys_match": cpu.unresolved_model_keys == gpu.unresolved_model_keys,
    }


def _quality_counts(records: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.offset_quality] = counts.get(record.offset_quality, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
