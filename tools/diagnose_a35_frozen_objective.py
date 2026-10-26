"""Evaluator-only comparison of frozen truth and saved post-search candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pyproj import Geod, Transformer

from bearing_localization.search.config_serialization import read_search_config
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.contracts.truth import EvaluationTruth
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

EXPECTED_HASHES = {
    "measurements.json": "ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1",
    "map.json": "cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93",
    "catalog.json": "2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704",
    "scope-manifest.json": "d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1",
    "estimate.json": "dec727542052eb0e1b8461bc1a8fb9fe99e3bd35fea617dd24841c4e9459f738",
    "search-config.json": "5be54ed68d6048edc3d35e64a9dfd408fda916c538650411af4b35e361ee1104",
    "search-run.json": "b7d3ccfd49ac853c8c0e317243fbf90aa0914674125f878cfdacd0784a1d5ab8",
}
_TRIAL_ID = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
_GEOD = Geod(ellps="WGS84")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _geodesic_error_m(
    candidate_xy: tuple[float, float],
    truth_xy: tuple[float, float],
    *,
    candidate_crs: str,
    truth_crs: str,
) -> float:
    candidate_lon, candidate_lat = Transformer.from_crs(
        candidate_crs, "EPSG:4326", always_xy=True
    ).transform(*candidate_xy)
    truth_lon, truth_lat = Transformer.from_crs(truth_crs, "EPSG:4326", always_xy=True).transform(
        *truth_xy
    )
    return abs(_GEOD.inv(truth_lon, truth_lat, candidate_lon, candidate_lat)[2])


def _spatial_modes(hypotheses: list[dict[str, Any]], *, radius_m: float = 100.0) -> list[list[int]]:
    """Connected components of saved hypotheses within a geodesic radius."""
    parent = list(range(len(hypotheses)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    transformer = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    geographic = [
        transformer.transform(float(row["east_m"]), float(row["north_m"])) for row in hypotheses
    ]
    for left in range(len(hypotheses)):
        lon_a, lat_a = geographic[left]
        for right in range(left + 1, len(hypotheses)):
            lon_b, lat_b = geographic[right]
            if abs(lat_a - lat_b) > radius_m / 100_000:
                continue
            if abs(_GEOD.inv(lon_a, lat_a, lon_b, lat_b)[2]) <= radius_m:
                join(left, right)
    groups: dict[int, list[int]] = {}
    for index in range(len(hypotheses)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda group: min(group))


def _hypothesis_record(index: int, row: dict[str, Any], error_m: float) -> dict[str, Any]:
    components = row.get("score_components") or {}
    matches = row.get("assignments") or []
    return {
        "rank": index + 1,
        "position_xy_m": [row["east_m"], row["north_m"]],
        "geodesic_error_m": error_m,
        "score": row["score"],
        "score_components": components,
        "matched_report_count": len(matches),
        "matches": [
            {
                "report_index": match["report_index"],
                "catalog_key": match["catalog_key"],
                "residual_deg": match["residual_deg"],
                "cost": match["cost"],
            }
            for match in matches
        ],
    }


def diagnose(
    *,
    static_input_dir: Path,
    estimate_path: Path,
    truth_path: Path,
    truth_crs_path: Path,
    truth_diagnostics_path: Path,
    output_path: Path,
    prior_estimate_path: Path | None = None,
    prior_diagnostic_path: Path | None = None,
    prior_truth_path: Path | None = None,
    prior_telemetry_path: Path | None = None,
) -> dict[str, Any]:
    """Score only fixed post-search points and evaluator truth; never proposes candidates."""
    checked_paths = {
        name: static_input_dir / name
        for name in EXPECTED_HASHES
        if name not in {"estimate.json", "search-config.json", "search-run.json"}
    }
    checked_paths["estimate.json"] = estimate_path
    checked_paths["search-config.json"] = estimate_path.parent / "search-config.json"
    checked_paths["search-run.json"] = estimate_path.parent / "search-run.json"
    actual_hashes = {name: _sha256(path) for name, path in checked_paths.items()}
    if actual_hashes != EXPECTED_HASHES:
        raise ValueError("Frozen A26 inputs or estimate do not match the declared hashes.")

    measurements = read_contract(static_input_dir / "measurements.json", MeasurementSetV2)
    catalog = read_contract(static_input_dir / "catalog.json", CatalogV2)
    map_input = read_contract(static_input_dir / "map.json", MapV2)
    config = read_search_config(estimate_path.parent / "search-config.json")
    truth = read_contract(truth_path, EvaluationTruth)
    truth_crs = json.loads(truth_crs_path.read_text(encoding="utf-8"))
    if truth.trial_id != _TRIAL_ID or truth_crs.get("trial_id") != _TRIAL_ID:
        raise ValueError("Evaluator truth does not identify the frozen development trial.")
    source_crs = truth_crs.get("coordinate_reference_system")
    if not isinstance(source_crs, str) or not source_crs:
        raise ValueError("Truth CRS metadata must declare its coordinate reference system.")
    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    if estimate.get("objective_version") != config.objective_version:
        raise ValueError("Estimate and frozen SearchConfig objective versions differ.")
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    to_search = Transformer.from_crs(source_crs, "EPSG:32618", always_xy=True)
    truth_xy = to_search.transform(truth.observer_east_m, truth.observer_north_m)
    truth_eval = scorer.evaluate(truth_xy)
    hypotheses = estimate.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        raise ValueError("The frozen estimate must contain ranked hypotheses.")
    truth_lon, truth_lat = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True).transform(
        truth.observer_east_m, truth.observer_north_m
    )
    scored_rows = [
        _hypothesis_record(
            index,
            row,
            _geodesic_error_m(
                (float(row["east_m"]), float(row["north_m"])),
                (truth.observer_east_m, truth.observer_north_m),
                candidate_crs="EPSG:32618",
                truth_crs=source_crs,
            ),
        )
        for index, row in enumerate(hypotheses)
    ]
    modes = _spatial_modes(hypotheses)
    mode_rows = []
    for mode_id, indices in enumerate(modes):
        best = min(indices, key=lambda index: float(hypotheses[index]["score"]))
        mode_rows.append(
            {"mode_id": mode_id, "candidate_ranks": [i + 1 for i in indices], **scored_rows[best]}
        )
    closest = min(scored_rows, key=lambda row: row["geodesic_error_m"])
    top = scored_rows[0]

    truth_groups_doc = json.loads(truth_diagnostics_path.read_text(encoding="utf-8"))
    truth_groups = {
        int(group["observation_index"]): set(group["catalog_member_keys"])
        for group in truth_groups_doc.get("groups", [])
        if group.get("detected") is True
    }
    truth_components = (
        truth_eval.hypothesis.score_components if truth_eval.hypothesis is not None else None
    )
    truth_prediction: dict[str, Any] = {
        "status": truth_eval.status,
        "reason": truth_eval.reason,
        "truth_position_search_crs": list(truth_xy),
        "truth_position_source_crs": [truth.observer_east_m, truth.observer_north_m],
        "source_crs": source_crs,
        "search_crs": "EPSG:32618",
        "map_water_status": vector_map.point_water_status(truth_xy),
        "coverage_status": truth_eval.predicted.coverage_status if truth_eval.predicted else None,
        "predicted_group_count": len(truth_eval.predicted.groups) if truth_eval.predicted else None,
        "objective_score": truth_eval.hypothesis.score if truth_eval.hypothesis else None,
        "score_components": (
            {
                "matched_cost": truth_components.matched_cost,
                "clutter_cost": truth_components.clutter_cost,
                "missed_cost": truth_components.missed_cost,
                "total_cost": truth_components.total_cost,
            }
            if truth_components is not None
            else None
        ),
        "matches": [],
        "predicted_groups": [],
        "report_sigmas_deg": [item.sigma_deg for item in measurements.measurements],
        "simulator_detected_group_count": len(truth_groups),
        "truth_lat_lon_deg": [truth_lat, truth_lon],
    }
    if truth_eval.predicted is not None:
        truth_prediction["predicted_groups"] = [
            {
                "bearing_deg": group.bearing_deg,
                "range_m": group.range_m,
                "representative_key": group.representative_key,
                "member_keys": list(group.member_keys),
            }
            for group in truth_eval.predicted.groups
        ]
    if truth_eval.hypothesis is not None and truth_eval.predicted is not None:
        for assignment in truth_eval.hypothesis.assignments:
            group = next(
                group
                for group in truth_eval.predicted.groups
                if group.representative_key == assignment.catalog_key
            )
            expected = truth_groups.get(assignment.report_index, set())
            predicted = set(group.member_keys)
            truth_prediction["matches"].append(
                {
                    "report_index": assignment.report_index,
                    "catalog_key": assignment.catalog_key,
                    "residual_deg": assignment.residual_deg,
                    "cost": assignment.cost,
                    "truth_member_keys": sorted(expected),
                    "predicted_member_keys": sorted(predicted),
                    "member_set_exact_match": predicted == expected,
                }
            )

    # Exact scorer parity at two saved, post-search candidates: the score winner and
    # the nearest saved hypothesis chosen only for this evaluator diagnosis.
    checked_ranks = sorted({top["rank"], closest["rank"]})
    saved_candidate_checks = []
    for rank in checked_ranks:
        row = hypotheses[rank - 1]
        evaluation = scorer.evaluate((float(row["east_m"]), float(row["north_m"])))
        saved_candidate_checks.append(
            {
                "rank": rank,
                "status": evaluation.status,
                "saved_score": row["score"],
                "recomputed_score": evaluation.hypothesis.score if evaluation.hypothesis else None,
                "predicted_group_count": len(evaluation.predicted.groups)
                if evaluation.predicted
                else None,
                "matched_report_count": len(evaluation.hypothesis.assignments)
                if evaluation.hypothesis
                else None,
                "matches": [
                    {
                        "report_index": match.report_index,
                        "catalog_key": match.catalog_key,
                    }
                    for match in (
                        evaluation.hypothesis.assignments if evaluation.hypothesis else ()
                    )
                ],
            }
        )

    prior_a43: dict[str, Any] | None = None
    prior_paths = (
        prior_estimate_path,
        prior_diagnostic_path,
        prior_truth_path,
        prior_telemetry_path,
    )
    if any(path is not None for path in prior_paths):
        if any(path is None for path in prior_paths):
            raise ValueError("A43 correction requires estimate, diagnostic, truth, and telemetry.")
        assert prior_estimate_path is not None
        assert prior_diagnostic_path is not None
        assert prior_truth_path is not None
        assert prior_telemetry_path is not None
        prior_document = json.loads(prior_estimate_path.read_text(encoding="utf-8"))
        prior_report = json.loads(prior_diagnostic_path.read_text(encoding="utf-8"))
        prior_truth = read_contract(prior_truth_path, EvaluationTruth)
        prior_telemetry = json.loads(prior_telemetry_path.read_text(encoding="utf-8"))
        if prior_report.get("estimate_sha256") != _sha256(prior_estimate_path):
            raise ValueError("A43 saved diagnostic does not bind to its estimate hash.")
        if prior_report.get("truth_sha256") != _sha256(prior_truth_path):
            raise ValueError("A43 saved diagnostic does not bind to its truth hash.")
        if (
            prior_truth.trial_id != truth.trial_id
            or prior_truth.observer_east_m != truth.observer_east_m
            or prior_truth.observer_north_m != truth.observer_north_m
        ):
            raise ValueError("A43 truth is not the same frozen observer position.")
        if (
            prior_telemetry.get("measurement_sha256") != EXPECTED_HASHES["measurements.json"]
            or prior_telemetry.get("map_sha256") != EXPECTED_HASHES["map.json"]
            or prior_telemetry.get("catalog_sha256") != EXPECTED_HASHES["catalog.json"]
        ):
            raise ValueError("A43 result does not use the exact frozen search inputs.")
        prior_rows = prior_document.get("hypotheses")
        if not isinstance(prior_rows, list) or not prior_rows:
            raise ValueError("A43 estimate must contain saved hypotheses.")
        prior_scored = [
            _hypothesis_record(
                index,
                row,
                _geodesic_error_m(
                    (float(row["east_m"]), float(row["north_m"])),
                    (prior_truth.observer_east_m, prior_truth.observer_north_m),
                    candidate_crs="EPSG:32618",
                    truth_crs=source_crs,
                ),
            )
            for index, row in enumerate(prior_rows)
        ]
        prior_a43 = {
            "estimate_sha256": _sha256(prior_estimate_path),
            "truth_sha256": _sha256(prior_truth_path),
            "truth_coordinate_source_crs": source_crs,
            "same_measurement_map_catalog_hashes": True,
            "original_reported_top_error_m": prior_report["score_rank_0"]["geodesic_error_m"],
            "original_reported_minimum_error_m": prior_report["minimum_position_error_diagnostic"][
                "geodesic_error_m"
            ],
            "corrected_top_error_m": prior_scored[0]["geodesic_error_m"],
            "corrected_minimum_error_m": min(row["geodesic_error_m"] for row in prior_scored),
            "hypotheses": prior_scored,
        }

    result = {
        "schema": "a35-frozen-objective-diagnostic-v1",
        "trial_id": _TRIAL_ID,
        "input_sha256": actual_hashes,
        "truth_sha256": _sha256(truth_path),
        "truth_crs_metadata_sha256": _sha256(truth_crs_path),
        "truth_diagnostics_sha256": _sha256(truth_diagnostics_path),
        "truth_prediction": truth_prediction,
        "prior_a43_post_search_diagnostic": prior_a43,
        "estimate": {
            "search_status": estimate.get("search_status"),
            "decision_status": estimate.get("decision_status"),
            "budget_used": estimate.get("budget_used"),
            "budget_limit": estimate.get("budget_limit"),
            "hypothesis_count": len(hypotheses),
            "hypotheses": scored_rows,
            "spatial_mode_radius_m": 100.0,
            "spatial_mode_count": len(modes),
            "spatial_modes": mode_rows,
            "top_score_hypothesis": top,
            "closest_hypothesis": closest,
            "saved_candidate_checks": saved_candidate_checks,
        },
        "diagnostic_scope": (
            "post-search evaluator-only; truth was not passed to candidate generation or search"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-input-dir", type=Path, required=True)
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--truth-crs", type=Path, required=True)
    parser.add_argument("--truth-diagnostics", type=Path, required=True)
    parser.add_argument("--prior-a43-estimate", type=Path)
    parser.add_argument("--prior-a43-diagnostic", type=Path)
    parser.add_argument("--prior-a43-truth", type=Path)
    parser.add_argument("--prior-a43-telemetry", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(
        static_input_dir=args.static_input_dir,
        estimate_path=args.estimate,
        truth_path=args.truth,
        truth_crs_path=args.truth_crs,
        truth_diagnostics_path=args.truth_diagnostics,
        output_path=args.output,
        prior_estimate_path=args.prior_a43_estimate,
        prior_diagnostic_path=args.prior_a43_diagnostic,
        prior_truth_path=args.prior_a43_truth,
        prior_telemetry_path=args.prior_a43_telemetry,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "hypothesis_count": result["estimate"]["hypothesis_count"],
                "spatial_mode_count": result["estimate"]["spatial_mode_count"],
                "truth_score": result["truth_prediction"]["objective_score"],
                "top_score": result["estimate"]["top_score_hypothesis"]["score"],
                "closest_error_m": result["estimate"]["closest_hypothesis"]["geodesic_error_m"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
