"""Evaluator-only audit of assignment-conditioned refinement of cached modes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, cast

from tools.evaluate_a20_cached_refinement import _metrics, load_refinement
from tools.evaluate_cached_finite_domain import (
    LoadedRun,
    decode_hypothesis,
    geodesic_error_m,
    load_run,
    read_json,
    read_jsonl,
    sha256_file,
)

ASSIGNED_SCHEMA = "assigned-bearing-refinement-diagnostic-v1"
START_LABELS = ("cached_hypothesis", "refined_mode.hypothesis")


def _same_assignment_payload(hyp: Any, payload: Any) -> bool:
    expected = [
        {
            "report_index": a.report_index,
            "catalog_key": a.catalog_key,
            "residual_deg": a.residual_deg,
            "cost": a.cost,
        }
        for a in hyp.assignments
    ]
    return payload == expected


def validate_start_evidence(start: dict[str, Any], expected: Any) -> tuple[Any, Any]:
    initial = decode_hypothesis(start.get("initial_hypothesis"))
    final = decode_hypothesis(start.get("final_hypothesis"))
    if (
        (initial.east_m, initial.north_m) != (expected.east_m, expected.north_m)
        or not math.isclose(initial.score, expected.score, rel_tol=1e-10, abs_tol=1e-8)
        or initial.assignments != expected.assignments
    ):
        raise ValueError("Assigned start differs from cached/A20 refined hypothesis.")
    if not _same_assignment_payload(initial, start.get("initial_assignments")):
        raise ValueError("Assigned initial assignment snapshot differs from hypothesis.")
    if not _same_assignment_payload(final, start.get("final_assignments")):
        raise ValueError("Assigned final assignment snapshot differs from hypothesis.")
    steps = start.get("steps")
    if not isinstance(steps, list) or len(steps) > 5:
        raise ValueError("Assigned refinement exceeds five steps.")
    current = initial
    for expected_step, step in enumerate(steps, 1):
        if step.get("step") != expected_step:
            raise ValueError("Assigned step sequence is not contiguous.")
        if step.get("original_hypothesis") is not None:
            original = decode_hypothesis(step["original_hypothesis"])
            if original != current:
                raise ValueError("Proposal was not based on the current exact hypothesis.")
        accepted = step.get("accepted_for_next_step") is True
        if accepted:
            exact = decode_hypothesis(step.get("exact_hypothesis"))
            if step.get("status") != "improved" or step.get("numerical_status") != "improved":
                raise ValueError("Accepted proposal lacks improved solver status.")
            if exact.score >= current.score:
                raise ValueError("Accepted exact step does not improve the objective.")
            if step.get("proposed_pose") != [exact.east_m, exact.north_m]:
                raise ValueError("Accepted exact proposal differs from its evaluated pose.")
            current = exact
        elif step.get("exact_hypothesis") is not None:
            decode_hypothesis(step["exact_hypothesis"])
    if final != current:
        raise ValueError("Final assigned hypothesis differs from accepted exact steps.")
    return initial, final


def load_assigned(
    root: Path,
    source_root: Path,
    a20_root: Path,
    run: LoadedRun,
    a20_manifest: dict[str, Any],
    a20_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != ASSIGNED_SCHEMA:
        raise ValueError("Unsupported assigned-refinement manifest.")
    if manifest.get("status") != "completed" or manifest.get("completed_mode_count") != 64:
        raise ValueError("Assigned refinement did not complete all 64 modes.")
    if (
        manifest.get("full_domain_complete") is not False
        or manifest.get("certificate_scope") != "none"
    ):
        raise ValueError("Assigned refinement cannot claim full-domain or certificate completion.")
    if manifest.get("truth_used") is not False or manifest.get("evaluator_used") is not False:
        raise ValueError("Assigned refinement manifest violates the truth boundary.")
    if manifest.get("mode_count") != 64 or manifest.get("max_steps_per_start") != 5:
        raise ValueError("Assigned refinement cap or step limit differs from the frozen contract.")
    if Path(manifest.get("a20_root", "")).resolve() != a20_root.resolve():
        raise ValueError("Assigned refinement points to a different A20 artifact.")
    a20_manifest_path = a20_root / "manifest.json"
    if manifest.get("a20_manifest_sha256") != sha256_file(a20_manifest_path):
        raise ValueError("Assigned refinement A20 manifest hash mismatch.")
    if manifest.get("a20_rows_sha256") != a20_manifest.get("rows_sha256"):
        raise ValueError("Assigned refinement A20 rows hash mismatch.")
    source_pins = manifest.get("source_pins")
    if not isinstance(source_pins, dict):
        raise ValueError("Assigned refinement source pins are missing.")
    supervisor_path = source_root / "supervisor-manifest.json"
    if source_pins.get("attempt_supervisor_sha256") != sha256_file(supervisor_path):
        raise ValueError("Assigned refinement source supervisor hash mismatch.")
    if source_pins.get("attempt_identity_sha256") != run.manifest.get("run_identity_sha256"):
        raise ValueError("Assigned refinement source identity mismatch.")
    if source_pins.get("frozen_scoring_source_sha") != run.identity.get("scoring_source_sha"):
        raise ValueError("Assigned refinement package source differs from the source run.")
    rows_file = manifest.get("rows_file")
    if not isinstance(rows_file, str) or Path(rows_file).name != rows_file:
        raise ValueError("Assigned refinement rows_file must be a safe basename.")
    rows_path = root / rows_file
    if not rows_path.is_file() or manifest.get("rows_sha256") != sha256_file(rows_path):
        raise ValueError("Assigned refinement rows hash mismatch.")
    rows = read_jsonl(rows_path)
    if len(rows) != 64:
        raise ValueError("Assigned refinement must contain exactly 64 rows.")
    ranked = manifest.get("ranked_selected_modes")
    if not isinstance(ranked, list) or len(ranked) != 64:
        raise ValueError("Assigned ranking does not cover all 64 selected modes.")
    if ranked != sorted(ranked, key=lambda item: (item.get("score"), item.get("mode_index"))):
        raise ValueError("Assigned selected-mode ranking is not deterministic.")

    source_by_index = {item.input_index: item for item in run.candidates}
    if len(a20_rows) != 64:
        raise ValueError("A20 comparison rows must contain all 64 modes.")
    for index, (row, a20_row) in enumerate(zip(rows, a20_rows, strict=True)):
        if row.get("mode_index") != index or row.get("input_index") != a20_row.get("input_index"):
            raise ValueError("Assigned rows differ from deterministic A20 selection order.")
        if row.get("candidate_id") != a20_row.get("candidate_id"):
            raise ValueError("Assigned row candidate differs from A20.")
        source = source_by_index[row["input_index"]]
        if row.get("chunk_name") != a20_row.get("chunk_name"):
            raise ValueError("Assigned row source chunk differs from A20.")
        for key in ("chunk_sha256", "sidecar_sha256"):
            if row.get(key) != a20_row.get(key):
                raise ValueError(f"Assigned row source hash differs from A20: {key}.")
        starts = row.get("starts")
        if not isinstance(starts, list) or [item.get("label") for item in starts] != list(
            START_LABELS
        ):
            raise ValueError("Assigned start labels differ from the required pair.")
        expected_starts = (
            source.hypothesis,
            decode_hypothesis(a20_row["refined_mode"]["hypothesis"]),
        )
        for start, expected in zip(starts, expected_starts, strict=True):
            initial, final = validate_start_evidence(start, expected)
        best = decode_hypothesis(row.get("best_hypothesis"))
        expected_start = min(
            starts, key=lambda item: (item["final_hypothesis"]["score"], item["label"])
        )
        expected_best = decode_hypothesis(expected_start["final_hypothesis"])
        if row.get("best_source") != expected_start["label"] or best != expected_best:
            raise ValueError("Per-mode best hypothesis differs from its two final starts.")
    ranked_expected = [
        {
            "mode_index": row["mode_index"],
            "candidate_id": row["candidate_id"],
            "best_source": row["best_source"],
            "score": row["best_hypothesis"]["score"],
            "east_m": row["best_hypothesis"]["east_m"],
            "north_m": row["best_hypothesis"]["north_m"],
            "assignments": row["best_hypothesis"]["assignments"],
        }
        for row in sorted(
            rows, key=lambda item: (item["best_hypothesis"]["score"], item["mode_index"])
        )
    ]
    if manifest["ranked_selected_modes"] != ranked_expected:
        raise ValueError("Manifest ranking differs from the exact selected-mode rows.")
    return manifest, rows


def _stage_metrics(
    raw: Any, truth_xy: tuple[float, float], ids: dict[int, set[str]]
) -> dict[str, Any] | None:
    return _metrics(raw, truth_xy, ids)


def evaluate_assigned(
    rows: list[dict[str, Any]],
    source_run: LoadedRun,
    a20_rows: list[dict[str, Any]],
    *,
    truth_xy: tuple[float, float],
    truth_ids: dict[int, set[str]],
    vector_map: Any,
) -> list[dict[str, Any]]:
    saved_rank = {item.input_index: rank for rank, item in enumerate(source_run.candidates, 1)}
    result: list[dict[str, Any]] = []
    for row, a20_row in zip(rows, a20_rows, strict=True):
        starts = {item["label"]: item for item in row["starts"]}
        cached_final = starts[START_LABELS[0]]["final_hypothesis"]
        a20_final = starts[START_LABELS[1]]["final_hypothesis"]

        def metric(raw: Any) -> dict[str, Any] | None:
            return _stage_metrics(raw, truth_xy, truth_ids)

        evaluated_starts = []
        for start in row["starts"]:
            steps = []
            for step in start["steps"]:
                pose = step.get("proposed_pose")
                pose_evidence = None
                if isinstance(pose, list) and len(pose) == 2:
                    east_m, north_m = float(pose[0]), float(pose[1])
                    pose_evidence = {
                        "position_xy_m": [east_m, north_m],
                        "geodesic_error_m": geodesic_error_m(
                            (east_m, north_m),
                            truth_xy,
                            candidate_crs="EPSG:32618",
                            truth_crs="EPSG:32619",
                        ),
                        "map_water_status": vector_map.point_water_status((east_m, north_m)),
                    }
                steps.append(
                    {
                        "step": step.get("step"),
                        "status": step.get("status"),
                        "numerical_status": step.get("numerical_status"),
                        "accepted_for_next_step": step.get("accepted_for_next_step") is True,
                        "canonical_evaluations": step.get("canonical_evaluations"),
                        "solver_exact_candidate": metric(step["exact_hypothesis"])
                        if step.get("exact_hypothesis") is not None
                        else None,
                        "proposed_pose_evidence": pose_evidence,
                        "original_hypothesis": metric(step["original_hypothesis"])
                        if step.get("original_hypothesis") is not None
                        else None,
                    }
                )
            evaluated_starts.append(
                {
                    "label": start["label"],
                    "stop_reason": start["stop_reason"],
                    "steps": steps,
                    "final": metric(start["final_hypothesis"]),
                }
            )
        result.append(
            {
                "mode_index": row["mode_index"],
                "input_index": row["input_index"],
                "candidate_id": row["candidate_id"],
                "source_score_rank": saved_rank[row["input_index"]],
                "cached_source": metric(a20_row["cached_hypothesis"]),
                "a20_continuous_refined": metric(a20_row["refined_mode"]["hypothesis"]),
                "assigned_from_cached": metric(cached_final),
                "assigned_from_a20_refined": metric(a20_final),
                "best_assigned": metric(row["best_hypothesis"]),
                "best_source": row["best_source"],
                "starts": evaluated_starts,
            }
        )
    rank_rows = sorted(
        (cast(dict[str, Any], row["best_assigned"])["score"], row["mode_index"]) for row in result
    )
    ranks = {index: rank for rank, (_, index) in enumerate(rank_rows, 1)}
    for row in result:
        row["best_assigned_score_rank_within_selected"] = ranks[row["mode_index"]]
    for field, score_key, error_key in (
        ("cached_source", "cached_source", "cached_source"),
        ("a20_continuous_refined", "a20_continuous_refined", "a20_continuous_refined"),
        ("assigned_from_cached", "assigned_from_cached", "assigned_from_cached"),
        ("assigned_from_a20_refined", "assigned_from_a20_refined", "assigned_from_a20_refined"),
        ("best_assigned", "best_assigned", "best_assigned"),
    ):
        score_order = sorted(
            result, key=lambda item: (item[score_key]["score"], item["mode_index"])
        )
        error_order = sorted(
            result, key=lambda item: (item[error_key]["geodesic_error_m"], item["mode_index"])
        )
        score_ranks = {item["mode_index"]: rank for rank, item in enumerate(score_order, 1)}
        error_ranks = {item["mode_index"]: rank for rank, item in enumerate(error_order, 1)}
        for item in result:
            item[f"{field}_score_rank_within_selected"] = score_ranks[item["mode_index"]]
            item[f"{field}_error_rank_within_selected"] = error_ranks[item["mode_index"]]
    return result


def bearing_information_summary(
    observer_xy: tuple[float, float],
    report_coordinates: dict[int, tuple[float, float]],
    report_indices: list[int],
    sigma_deg: float,
) -> dict[str, Any]:
    """Linearized independent-bearing information at a known point, for diagnosis only."""
    weight = 1.0 / math.radians(sigma_deg) ** 2
    i_ee = i_en = i_nn = 0.0
    bearings = []
    ranges = []
    for index in report_indices:
        east, north = report_coordinates[index]
        de = east - observer_xy[0]
        dn = north - observer_xy[1]
        radius_sq = de * de + dn * dn
        if radius_sq <= 0:
            raise ValueError("Known report group coincides with the observer.")
        j_e = dn / radius_sq
        j_n = -de / radius_sq
        i_ee += weight * j_e * j_e
        i_en += weight * j_e * j_n
        i_nn += weight * j_n * j_n
        bearings.append(math.degrees(math.atan2(de, dn)) % 360.0)
        ranges.append(math.sqrt(radius_sq))
    trace = i_ee + i_nn
    disc = math.sqrt((i_ee - i_nn) ** 2 + 4.0 * i_en**2)
    largest = (trace + disc) / 2.0
    smallest = (trace - disc) / 2.0
    if smallest <= 0:
        raise ValueError("Known-report bearing geometry is rank deficient.")
    return {
        "report_indices": report_indices,
        "sigma_deg_assumed": sigma_deg,
        "bearing_deg_from_truth": bearings,
        "range_m_from_truth": ranges,
        "information_eigenvalues_per_m2": [largest, smallest],
        "linearized_95_percent_semiaxes_m": [
            math.sqrt(5.991 / smallest),
            math.sqrt(5.991 / largest),
        ],
        "condition_number": largest / smallest,
        "interpretation": (
            "evaluator-only linearized geometry diagnostic; not an estimator covariance"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source-root",
        "a20-root",
        "assigned-root",
        "truth-dir",
        "expected-bindings",
        "static-input-dir",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run = load_run(args.source_root, expected_bindings=read_json(args.expected_bindings))
    a20_manifest, a20_rows = load_refinement(args.a20_root, args.source_root, run)
    manifest, rows = load_assigned(
        args.assigned_root, args.source_root, args.a20_root, run, a20_manifest, a20_rows
    )
    # Evaluator truth is read only after all run/A20/assigned bindings validate.
    truth = read_json(args.truth_dir / "truth.json")
    support_path = args.truth_dir / "support.json"
    support = read_json(support_path)
    crs_path = args.truth_dir / "truth-crs.json"
    crs = read_json(crs_path)
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    truth_contract = read_contract(args.truth_dir / "truth.json", EvaluationTruth)
    trial_id = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
    if (
        truth_contract.trial_id != trial_id
        or crs.get("coordinate_reference_system") != "EPSG:32619"
    ):
        raise ValueError("Evaluator truth or CRS differs from frozen trial.")
    if (
        support.get("supported") is not True
        or support.get("valid_reference_water") is not True
        or support.get("port_id") != "port-001"
        or support.get("placement_id") != "placement-001"
        or type(support.get("detected_physical_catalog_matched_groups")) is not int
    ):
        raise ValueError("Frozen evaluator support is invalid or belongs to a different trial.")
    if truth.get("trial_id") != trial_id:
        raise ValueError("Truth does not identify the frozen development trial.")
    ids: dict[int, set[str]] = {}
    for obj in truth.get("objects", []):
        if obj.get("observation_index") is not None:
            key = str(obj.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                ids.setdefault(int(obj["observation_index"]), set()).add(key)
    from maritime_localization_shared.contracts.inputs_v2 import MapV2
    from maritime_localization_shared.geospatial.vector_map import VectorMap

    map_path = args.static_input_dir / "map.json"
    if sha256_file(map_path) != run.identity["static_input_sha256"]["map"]:
        raise ValueError("Evaluator map differs from the immutable source-run map.")
    map_input = read_contract(map_path, MapV2)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    report_coordinates = {
        int(obj["observation_index"]): (float(obj["east_m"]), float(obj["north_m"]))
        for obj in truth.get("objects", [])
        if obj.get("observation_index") is not None
        and obj.get("east_m") is not None
        and obj.get("north_m") is not None
    }
    four_report_indices = [0, 1, 2, 4]
    all_five_indices = [0, 1, 2, 3, 4]
    geometry = {
        "four_matched_reports_0_1_2_4": bearing_information_summary(
            (truth_contract.observer_east_m, truth_contract.observer_north_m),
            report_coordinates,
            four_report_indices,
            0.5,
        ),
        "all_five_reports": bearing_information_summary(
            (truth_contract.observer_east_m, truth_contract.observer_north_m),
            report_coordinates,
            all_five_indices,
            0.5,
        ),
    }
    result = {
        "schema": "a35-assigned-refinement-evaluation-v1",
        "evaluation_only": True,
        "truth_used_for_refinement": False,
        "accepted_fix_claim": False,
        "source_run_complete": run.complete,
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "a20_manifest_sha256": sha256_file(args.a20_root / "manifest.json"),
        "assigned_manifest_sha256": sha256_file(args.assigned_root / "manifest.json"),
        "assigned_rows_sha256": manifest.get("rows_sha256"),
        "assigned_status": manifest.get("status"),
        "mode_count": manifest.get("completed_mode_count"),
        "explicit_mode_scope": 64,
        "full_domain_complete": False,
        "source_candidate_dispositions": run.disposition_counts,
        "evaluator_inputs_sha256": {
            "truth": sha256_file(args.truth_dir / "truth.json"),
            "support": sha256_file(support_path),
            "truth_crs": sha256_file(crs_path),
        },
        "known_group_geometry_at_truth": geometry,
        "mode_evaluations": evaluate_assigned(
            rows,
            run,
            a20_rows,
            truth_xy=(truth_contract.observer_east_m, truth_contract.observer_north_m),
            truth_ids=ids,
            vector_map=vector_map,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
