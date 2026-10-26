"""Evaluate saved A26 wedge-geodesic hypotheses against sealed development truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pyproj import Geod, Transformer


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--truth-dir", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--search-crs", default="EPSG:32618")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    estimate = read_json(args.estimate)
    truth = read_json(args.truth_dir / "truth.json")
    truth_crs = read_json(args.truth_dir / "truth-crs.json")
    measurements = read_json(args.measurements)
    source_crs = truth_crs["coordinate_reference_system"]
    truth_to_geo = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    search_to_geo = Transformer.from_crs(args.search_crs, "EPSG:4326", always_xy=True)
    truth_to_search = Transformer.from_crs(source_crs, args.search_crs, always_xy=True)
    geod = Geod(ellps="WGS84")

    observer_e = float(truth["observer_east_m"])
    observer_n = float(truth["observer_north_m"])
    observer_lon, observer_lat = truth_to_geo.transform(observer_e, observer_n)
    search_e, search_n = truth_to_search.transform(observer_e, observer_n)

    truth_objects = truth.get("objects", [])
    truth_ids_by_observation: dict[int, set[str]] = {}
    targets_by_observation: dict[int, dict[tuple[float, float], list[str]]] = {}
    for item in truth_objects:
        index = int(item["observation_index"])
        key = str(item.get("ais_record_id", "")).removeprefix("catalog:")
        if key:
            truth_ids_by_observation.setdefault(index, set()).add(key)
        point = (round(float(item["east_m"]), 6), round(float(item["north_m"]), 6))
        targets_by_observation.setdefault(index, {}).setdefault(point, []).append(
            str(item.get("object_id", ""))
        )

    residuals = []
    for index, measurement in enumerate(measurements["measurements"]):
        points = targets_by_observation.get(index, {})
        if len(points) != 1:
            raise ValueError(
                f"Expected one distinct truth location for observation {index}; got {len(points)}"
            )
        (target_e, target_n), object_ids = next(iter(points.items()))
        target_lon, target_lat = truth_to_geo.transform(target_e, target_n)
        azimuth, _, distance_m = geod.inv(observer_lon, observer_lat, target_lon, target_lat)
        true_bearing_deg = azimuth % 360.0
        observed_bearing_deg = float(measurement["bearing_deg"])
        residual_deg = (observed_bearing_deg - true_bearing_deg + 180.0) % 360.0 - 180.0
        sigma_deg = float(measurement["sigma_deg"])
        residuals.append(
            {
                "observation_index": index,
                "truth_object_ids": sorted(object_ids),
                "range_m": distance_m,
                "true_bearing_deg": true_bearing_deg,
                "observed_bearing_deg": observed_bearing_deg,
                "residual_deg": residual_deg,
                "sigma_deg": sigma_deg,
                "residual_sigma": residual_deg / sigma_deg,
            }
        )

    candidates = []
    for index, hypothesis in enumerate(estimate.get("hypotheses", [])):
        lon, lat = search_to_geo.transform(
            float(hypothesis["east_m"]), float(hypothesis["north_m"])
        )
        _, _, error_m = geod.inv(observer_lon, observer_lat, lon, lat)
        assignments = hypothesis.get("assignments", [])
        correct = sum(
            str(assignment["catalog_key"])
            in truth_ids_by_observation.get(int(assignment["report_index"]), set())
            for assignment in assignments
        )
        candidates.append(
            {
                "hypothesis_index": index,
                "score": float(hypothesis["score"]),
                "east_m": float(hypothesis["east_m"]),
                "north_m": float(hypothesis["north_m"]),
                "geodesic_error_m": error_m,
                "assignment_count": len(assignments),
                "truth_id_assignment_count": correct,
                "assignments": assignments,
            }
        )
    by_score = sorted(candidates, key=lambda item: (item["score"], item["hypothesis_index"]))
    by_error = sorted(
        candidates,
        key=lambda item: (item["geodesic_error_m"], item["hypothesis_index"]),
    )
    result = {
        "schema_version": 1,
        "evaluation_only": True,
        "search_truth_input": None,
        "estimate_sha256": digest(args.estimate),
        "truth_sha256": digest(args.truth_dir / "truth.json"),
        "truth_crs_sha256": digest(args.truth_dir / "truth-crs.json"),
        "measurements_sha256": digest(args.measurements),
        "search_crs": args.search_crs,
        "truth_crs": source_crs,
        "truth_xy_in_search_crs_m": [search_e, search_n],
        "search_status": estimate["search_status"],
        "decision_status": estimate["decision_status"],
        "candidate_count": len(candidates),
        "top_score": by_score[0] if by_score else None,
        "top_score_ranks": by_score[:5],
        "minimum_error": by_error[0] if by_error else None,
        "minimum_error_ranks": by_error[:5],
        "all_hypotheses": candidates,
        "known_association_residuals": residuals,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
