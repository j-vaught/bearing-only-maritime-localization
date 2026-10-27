"""Compare an experimental GPU pipeline against CPU candidate scoring."""

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
from maritime.search.gpu.shoreline import GpuShorelineIndex
from maritime.search.scoring import CandidateScorer
from maritime.serialization import read_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("measurements", "catalog", "map", "config", "candidates", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--max-candidates", type=int, default=1)
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
        cpu = CandidateScorer(measurements, catalog, vector_map, config)
        source = json.loads(args.candidates.read_text())
        positions = [
            (h["east_m"], h["north_m"]) for h in source["hypotheses"][: args.max_candidates]
        ]
        if not positions:
            raise ValueError("No input candidates.")
        records = [r for r in cpu._prediction_context.records if r.is_physical]
        geod = Geod(ellps="WGS84")
        ll = []
        for r in records:
            lon, lat = r.record.longitude_deg, r.record.latitude_deg
            if r.offset_quality != "unsupported" and (r.east_offset_m or r.north_offset_m):
                azimuth = degrees(atan2(r.east_offset_m, r.north_offset_m)) % 360.0
                lon, lat, _ = geod.fwd(lon, lat, azimuth, hypot(r.east_offset_m, r.north_offset_m))
            ll.append((lon, lat))
        forward = Transformer.from_crs("EPSG:4326", vector_map.crs, always_xy=True)
        inverse = Transformer.from_crs(vector_map.crs, "EPSG:4326", always_xy=True)
        lon, lat = inverse.transform([p[0] for p in positions], [p[1] for p in positions])
        candidates_ll = np.column_stack((lon, lat))
        tx, ty = forward.transform([p[0] for p in ll], [p[1] for p in ll])
        targets_xy = list(zip(tx, ty, strict=True))
        keys = [r.record.key for r in records]
        options: dict[str, Any] = dict(
            candidate_ids=tuple(range(len(positions))),
            object_keys=keys,
            object_types=[r.record.normalized_type or r.record.raw_type for r in records],
            visibility_radius_m=config.max_range_m,
            minimum_range_m=config.min_range_m,
            range_inclusive=config.range_inclusive,
            angular_resolution_deg=config.circular_merge_tolerance_deg,
            type_labels_enabled=config.use_types,
            angular_half_width_deg=[r.half_width_deg for r in records],
            catalog_uncertainty_m=[
                r.uncertainty_m if r.uncertainty_m is not None else np.nan for r in records
            ],
            unsupported_keys=[r.record.key for r in records if r.offset_quality == "unsupported"],
            identity_limited_keys=[r.record.key for r in records if r.identity_limited],
            offset_quality=[r.offset_quality for r in records],
        )
        index = GpuShorelineIndex(vector_map)
        setup_s = perf_counter() - start
        cpu_start = perf_counter()
        cpu_result = [cpu.evaluate(p) for p in positions]
        cpu_reference_s = perf_counter() - cpu_start
        t = perf_counter()
        preliminary = predict_candidates_cupy(
            candidates_ll,
            ll,
            visibility_state=np.ones((len(positions), len(ll)), dtype=np.uint8),
            **options,
        )
        mask = cp.asnumpy(preliminary.in_range)
        cp.cuda.get_current_stream().synchronize()
        range_s = perf_counter() - t
        t = perf_counter()
        water = index.classify_candidates(positions, device_id=args.device_id)
        visibility: Any = index.visibility_matrix(
            positions,
            targets_xy,
            in_range_mask=mask,
            device_id=args.device_id,
            return_device_array=True,
        )
        cp.cuda.get_current_stream().synchronize()
        visibility_s = perf_counter() - t
        t = perf_counter()
        pred = predict_candidates_cupy(
            candidates_ll, ll, visibility_state=visibility, observer_status=water, **options
        )
        pred_s = perf_counter() - t
        rows = []
        for i, p in enumerate(positions):
            result = cpu_result[i]
            entry = {
                "east_m": p[0],
                "north_m": p[1],
                "cpu_status": result.status,
                "gpu_coverage": pred.groups[i].coverage_status,
                "gpu_unresolved_model": pred.groups[i].unresolved_model,
                "cpu_group_count": len(result.predicted.groups) if result.predicted else None,
                "gpu_group_count": len(pred.groups[i].groups),
            }
            if (
                result.hypothesis is not None
                and pred.groups[i].coverage_status == "known"
                and not pred.groups[i].unresolved_model
            ):
                t2 = perf_counter()
                _, assignment = score_prediction_batch_cupy(measurements, [pred.groups[i]], config)
                cp.cuda.get_current_stream().synchronize()
                gpu_groups = pred.groups[i].groups
                cpu_groups = result.predicted.groups if result.predicted else ()
                gpu_matches = {
                    (r, gpu_groups[g].representative_key) for r, g in assignment.matches[0]
                }
                cpu_matches = {
                    (a.report_index, a.catalog_key) for a in result.hypothesis.assignments
                }
                entry.update(
                    group_members_equal=[g.member_keys for g in gpu_groups]
                    == [g.member_keys for g in cpu_groups],
                    assignments_equal=gpu_matches == cpu_matches,
                    cpu_score=result.hypothesis.score,
                    gpu_score=float(assignment.device_costs[0]),
                    gpu_assignment_s=perf_counter() - t2,
                    cpu_matches=len(result.hypothesis.assignments),
                    gpu_matches=len(assignment.matches[0]),
                )
            rows.append(entry)
        report = {
            "experimental": True,
            "candidate_count": len(positions),
            "catalog_physical_records": len(records),
            "in_range_pairs": int(mask.sum()),
            "setup_s": setup_s,
            "cpu_reference_s": cpu_reference_s,
            "gpu_visibility_cpu_fallbacks": index.visibility_cpu_fallback_count,
            "gpu_range_s": range_s,
            "gpu_visibility_s": visibility_s,
            "gpu_prediction_s": pred_s,
            "results": rows,
            "elapsed_wall_s": perf_counter() - start,
        }
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
