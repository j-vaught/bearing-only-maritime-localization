"""Build evaluator-only A24 calibration rows from search evidence and trial truth."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from tools.run_a38_execution import folder, hfile
from tools.run_accuracy_suite import _read_table

EVIDENCE_SCHEMA = "search-mode-evidence-v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _public_evidence(telemetry: dict[str, Any] | None) -> dict[str, Any] | None:
    if telemetry is None:
        return None
    diagnostics = telemetry.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    evidence = diagnostics.get("public_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        return None
    return evidence


def _score_tolerance(*scores: float) -> float:
    """Match the runtime accuracy-decision tolerance exactly."""
    return max(1e-12, 1e-10 * max((abs(score) for score in scores), default=0.0))


def _matches_top(mode: dict[str, Any], top: dict[str, Any]) -> bool:
    mode_east = _number(mode.get("east_m"))
    mode_north = _number(mode.get("north_m"))
    mode_score = _number(mode.get("score"))
    top_east = _number(top.get("east_m"))
    top_north = _number(top.get("north_m"))
    top_score = _number(top.get("score"))
    if any(
        value is None
        for value in (mode_east, mode_north, mode_score, top_east, top_north, top_score)
    ):
        return False
    assert mode_east is not None and mode_north is not None and mode_score is not None
    assert top_east is not None and top_north is not None and top_score is not None
    if math.hypot(mode_east - top_east, mode_north - top_north) > 1e-6:
        return False
    if abs(mode_score - top_score) > _score_tolerance(mode_score, top_score):
        return False
    assignments = top.get("assignments")
    mode_keys = mode.get("matched_catalog_keys")
    if not isinstance(assignments, list) or not isinstance(mode_keys, list):
        return False
    if any(not isinstance(key, str) or not key.strip() for key in mode_keys):
        return False
    assigned_keys = sorted(
        item["catalog_key"]
        for item in assignments
        if isinstance(item, dict) and isinstance(item.get("catalog_key"), str)
    )
    if (
        len(mode_keys) < 3
        or len(set(mode_keys)) != len(mode_keys)
        or len(assigned_keys) != len(assignments)
        or assigned_keys != sorted(mode_keys)
    ):
        return False
    uncertainty = top.get("uncertainty")
    top_radius = (
        _number(uncertainty.get("major_radius_m")) if isinstance(uncertainty, dict) else None
    )
    mode_radius = _number(mode.get("radius_95_m"))
    if top_radius is None or mode_radius is None:
        return False
    return abs(top_radius - mode_radius) <= max(1e-8, 1e-10 * max(top_radius, mode_radius))


def _raw_veto_gaps(
    evidence: dict[str, Any], top_score: float, top_east: float, top_north: float
) -> tuple[list[float] | None, float | None, str]:
    """Validate A33 veto telemetry and return gaps using the runtime predicate inputs."""
    veto = evidence.get("raw_distant_hypothesis_veto_evidence")
    if not isinstance(veto, dict):
        return None, None, "raw_distant_veto_evidence_missing"
    veto_top_score = _number(veto.get("top_score"))
    tolerance = _number(veto.get("score_tolerance"))
    expected_tolerance = _score_tolerance(top_score)
    if (
        veto_top_score is None
        or abs(veto_top_score - top_score) > expected_tolerance
        or tolerance is None
        or abs(tolerance - expected_tolerance) > max(1e-15, expected_tolerance * 1e-9)
    ):
        return None, None, "raw_distant_veto_metadata_mismatch"
    alternatives = veto.get("alternatives")
    if not isinstance(alternatives, list):
        return None, None, "raw_distant_veto_alternatives_malformed"
    assert top_east is not None and top_north is not None
    gaps: list[float] = []
    for alt in alternatives:
        if not isinstance(alt, dict):
            return None, None, "raw_distant_veto_alternative_malformed"
        east = _number(alt.get("east_m"))
        north = _number(alt.get("north_m"))
        score = _number(alt.get("score"))
        score_gap = _number(alt.get("score_gap"))
        distance = _number(alt.get("distance_from_top_m"))
        if any(value is None for value in (east, north, score, score_gap, distance)):
            return None, None, "raw_distant_veto_alternative_malformed"
        assert east is not None and north is not None and score is not None
        assert score_gap is not None and distance is not None
        expected_distance = math.hypot(east - top_east, north - top_north)
        expected_gap = score - top_score
        if (
            expected_distance < 100.0
            or abs(distance - expected_distance) > 1e-6
            or abs(score_gap - expected_gap) > expected_tolerance
        ):
            return None, None, "raw_distant_veto_alternative_mismatch"
        gaps.append(expected_gap)
    return gaps, expected_tolerance, "complete"


def evaluator_error_metrics(
    trial_id: str,
    evaluator_estimate: dict[str, Any] | None,
    truth: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
    *,
    source_estimate_sha256: str | None,
    source_truth_sha256: str | None,
    evaluator_estimate_sha256: str | None,
    projected_crs: str | None,
) -> tuple[float | None, float | None, str]:
    """Compute evaluator error only after verifying the coordinate transform contract."""
    if evaluator_estimate is None or truth is None or provenance is None:
        return None, None, "coordinate_artifact_missing"
    expected = {
        "schema_version": 1,
        "trial_id": trial_id,
        "source_estimate_sha256": source_estimate_sha256,
        "source_truth_sha256": source_truth_sha256,
        "evaluator_estimate_sha256": evaluator_estimate_sha256,
        "estimate_source_crs": projected_crs,
        "estimate_target_crs": provenance.get("truth_coordinate_crs"),
        "axis_order": "xy",
        "transform_method": "pyproj.Transformer.from_crs(always_xy=True)",
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        return None, None, "coordinate_provenance_mismatch"
    truth_crs = provenance.get("truth_coordinate_crs")
    if not isinstance(truth_crs, str) or not truth_crs:
        return None, None, "truth_coordinate_crs_missing"
    top = evaluator_estimate.get("hypotheses")
    if not isinstance(top, list) or not top or not isinstance(top[0], dict):
        return None, None, "evaluator_estimate_top_missing"
    estimate_east = _number(top[0].get("east_m"))
    estimate_north = _number(top[0].get("north_m"))
    truth_east = _number(truth.get("observer_east_m"))
    truth_north = _number(truth.get("observer_north_m"))
    if any(value is None for value in (estimate_east, estimate_north, truth_east, truth_north)):
        return None, None, "evaluator_coordinates_missing"
    assert estimate_east is not None and estimate_north is not None
    assert truth_east is not None and truth_north is not None
    from pyproj import CRS, Transformer

    crs = CRS.from_user_input(truth_crs)
    geographic = crs.geodetic_crs
    if geographic is None:
        return None, None, "truth_crs_has_no_geodetic_crs"
    to_geographic = Transformer.from_crs(crs, geographic, always_xy=True)
    estimate_lon, estimate_lat = to_geographic.transform(estimate_east, estimate_north)
    truth_lon, truth_lat = to_geographic.transform(truth_east, truth_north)
    geod = crs.get_geod()
    if geod is None:
        return None, None, "truth_crs_has_no_geodetic_calculator"
    geodesic_m = geod.inv(estimate_lon, estimate_lat, truth_lon, truth_lat)[2]
    euclidean_m = math.hypot(estimate_east - truth_east, estimate_north - truth_north)
    if not math.isfinite(geodesic_m) or not math.isfinite(euclidean_m):
        return None, None, "nonfinite_evaluator_error"
    return float(geodesic_m), float(euclidean_m), "verified_evaluator_coordinates_and_truth"


def build_trial_metrics(
    trial: dict[str, Any],
    *,
    run_state: dict[str, Any] | None,
    estimate: dict[str, Any] | None,
    telemetry: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    truth: dict[str, Any] | None,
    support: dict[str, Any] | None,
    evaluator_error_m: float | None = None,
    evaluator_euclidean_error_m: float | None = None,
    error_source: str | None = None,
) -> dict[str, Any]:
    """Derive one calibration row without giving truth to search-side evidence."""
    split = str(trial["split"])
    if split not in {"development", "validation"}:
        raise ValueError("Calibration metrics are restricted to development or validation.")
    row: dict[str, Any] = {
        "split": split,
        "trial_id": str(trial["trial_id"]),
        "port_id": str(trial["port_id"]),
        "placement_id": str(trial["placement_id"]),
        "realization": int(trial["realization"]),
        "range_m": float(trial["range_m"]),
        "report_types": trial["report_types"] == "True",
        "sensor_condition": str(trial["sensor_condition"]),
        "run_status": str((run_state or {}).get("status", "missing")),
        "failure": str((run_state or {}).get("failure", "")),
        "supported": (support or {}).get("supported") is True,
        "search_status": "failed",
        "distinct_matched_groups": 0,
        "ellipse_rank": None,
        "meaningful_distant_coverage": False,
        "score_gap": None,
        "candidate_outcome_counts_available": None,
        "unresolved_candidate_count": None,
        "unresolved_region_count": None,
        "coverage_complete": None,
        "global_bound_excludes_outside": None,
        "certificate_scope": None,
        "bound_gap": None,
        "global_exclusion_verified": False,
        "distant_region_score_lower_bound": None,
        "distant_region_bound_scope": None,
        "distant_region_bound_top_east_m": None,
        "distant_region_bound_top_north_m": None,
        "score_tolerance": None,
        "raw_distant_hypothesis_veto_evidence": None,
        "radius_95_m": None,
        "error_m": (
            evaluator_error_m
            if evaluator_error_m is not None and math.isfinite(evaluator_error_m)
            else None
        ),
        "euclidean_error_m": (
            evaluator_euclidean_error_m
            if evaluator_euclidean_error_m is not None
            and math.isfinite(evaluator_euclidean_error_m)
            else None
        ),
        "evidence_status": "missing",
        "error_source": error_source,
        "evaluation_acceptance_status": (evaluation or {}).get("acceptance_status"),
    }
    if estimate is None:
        row["evidence_status"] = "estimate_missing"
        return row

    status = estimate.get("search_status")
    row["search_status"] = (
        status if status in {"completed", "budget_exhausted", "failed"} else "failed"
    )
    hypotheses = estimate.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        row["evidence_status"] = "no_ranked_hypotheses"
        return row
    top = hypotheses[0]
    if not isinstance(top, dict):
        row["evidence_status"] = "malformed_top_hypothesis"
        return row
    top_east = _number(top.get("east_m"))
    top_north = _number(top.get("north_m"))
    top_score = _number(top.get("score"))
    assignments = top.get("assignments")
    if isinstance(assignments, list):
        row["distinct_matched_groups"] = len(
            {
                item.get("catalog_key")
                for item in assignments
                if isinstance(item, dict)
                and isinstance(item.get("catalog_key"), str)
                and item["catalog_key"].strip()
            }
        )
    evidence = _public_evidence(telemetry)
    if evidence is None:
        row["evidence_status"] = "public_mode_evidence_missing_or_unsupported_schema"
        return row
    if evidence.get("search_status") != row["search_status"]:
        row["evidence_status"] = "search_status_mismatch"
        return row

    row["scope_manifest_verified"] = evidence.get("scope_manifest_verified")
    if evidence.get("scope_manifest_verified") is not True:
        row["evidence_status"] = "full_domain_scope_not_verified"
        return row

    candidate_counts_available = evidence.get("candidate_outcome_counts_available")
    row["candidate_outcome_counts_available"] = candidate_counts_available
    unresolved = evidence.get("unresolved_candidate_count")
    row["unresolved_candidate_count"] = unresolved
    counts = evidence.get("candidate_outcome_counts")
    if type(candidate_counts_available) is not bool:
        row["evidence_status"] = "candidate_outcome_counts_availability_malformed"
        return row
    if candidate_counts_available:
        count_fields = ("scored", "invalid_water", "unresolved_coverage", "unresolved_model")
        if type(unresolved) is not int or unresolved < 0:
            row["evidence_status"] = "unresolved_candidate_count_malformed"
            return row
        if not isinstance(counts, dict) or any(
            type(counts.get(key)) is not int or counts[key] < 0 for key in count_fields
        ):
            row["evidence_status"] = "candidate_outcome_counts_malformed"
            return row
        if unresolved != counts["unresolved_coverage"] + counts["unresolved_model"]:
            row["evidence_status"] = "unresolved_candidate_count_mismatch"
            return row
    elif unresolved is not None or counts is not None:
        row["evidence_status"] = "candidate_outcome_counts_unavailable_but_populated"
        return row
    if unresolved:
        row["evidence_status"] = "unresolved_candidate_geometry"
        return row

    coverage_complete = evidence.get("coverage_complete")
    global_bound = evidence.get("global_bound_excludes_outside")
    unresolved_regions = evidence.get("unresolved_region_count")
    evaluated_candidates = evidence.get("evaluated_candidate_count")
    certificate_scope = evidence.get("certificate_scope")
    bound_gap = _number(evidence.get("bound_gap"))
    estimate_certificate = estimate.get("certificate_scope")
    estimate_bound_gap = _number(estimate.get("bound_gap"))
    distant_region_bound = _number(evidence.get("distant_region_score_lower_bound"))
    distant_region_scope = evidence.get("distant_region_bound_scope")
    distant_top_east = _number(evidence.get("distant_region_bound_top_east_m"))
    distant_top_north = _number(evidence.get("distant_region_bound_top_north_m"))
    row["coverage_complete"] = coverage_complete
    row["global_bound_excludes_outside"] = global_bound
    row["unresolved_region_count"] = unresolved_regions
    row["evaluated_candidate_count"] = evaluated_candidates
    row["certificate_scope"] = estimate_certificate
    row["bound_gap"] = estimate_bound_gap
    row["candidate_certificate_scope"] = estimate_certificate
    row["candidate_bound_gap"] = estimate_bound_gap
    row["distant_region_score_lower_bound"] = distant_region_bound
    row["distant_region_bound_scope"] = distant_region_scope
    row["distant_region_bound_top_east_m"] = distant_top_east
    row["distant_region_bound_top_north_m"] = distant_top_north
    if (
        type(coverage_complete) is not bool
        or type(global_bound) is not bool
        or type(unresolved_regions) is not int
        or unresolved_regions < 0
        or type(evaluated_candidates) is not int
        or evaluated_candidates < 0
    ):
        row["evidence_status"] = "coverage_evidence_malformed"
        return row
    if (
        certificate_scope != estimate_certificate
        or (bound_gap is None) != (estimate_bound_gap is None)
        or (
            bound_gap is not None
            and estimate_bound_gap is not None
            and abs(bound_gap - estimate_bound_gap) > 1e-8
        )
    ):
        row["evidence_status"] = "final_certificate_evidence_mismatch"
        return row
    global_exclusion = (
        row["search_status"] == "completed"
        and row["distinct_matched_groups"] >= 3
        and _number(
            (top.get("uncertainty") or {}).get("major_radius_m")
            if isinstance(top.get("uncertainty"), dict)
            else None
        )
        is not None
        and evaluated_candidates >= 2
        and coverage_complete is True
        and global_bound is True
        and unresolved_regions == 0
        and certificate_scope == "continuous_objective"
        and bound_gap is not None
        and bound_gap <= 1e-8
        and distant_region_bound is not None
        and distant_region_scope == "all_objective_positions_at_least_100m_from_top"
        and distant_region_bound is not None
        and top_score is not None
        and distant_region_bound > top_score
        and distant_top_east is not None
        and distant_top_north is not None
        and top_east is not None
        and top_north is not None
        and math.hypot(distant_top_east - top_east, distant_top_north - top_north) <= 1e-6
    )
    # The margin depends on each calibration grid threshold, so A24 applies its
    # shared runtime predicate to these preserved inputs per candidate row.
    row["global_exclusion_verified"] = False

    if top_score is None or top_east is None or top_north is None:
        row["evidence_status"] = "top_hypothesis_coordinates_or_score_malformed"
        return row
    veto_gaps, score_tolerance, veto_status = _raw_veto_gaps(
        evidence, top_score, top_east, top_north
    )
    if veto_gaps is None or score_tolerance is None:
        row["evidence_status"] = veto_status
        return row
    row["score_tolerance"] = score_tolerance
    row["raw_distant_hypothesis_veto_evidence"] = [
        {"score_gap": gap, "score_tolerance": score_tolerance} for gap in veto_gaps
    ]

    if global_exclusion:
        uncertainty = top.get("uncertainty")
        radius = _number(uncertainty.get("major_radius_m"))
        minor = _number(uncertainty.get("minor_radius_m"))
        row["radius_95_m"] = radius
        row["ellipse_rank"] = 2 if minor is not None and minor > 0.0 else 1
        row["meaningful_distant_coverage"] = False
        # A24 must apply the exact runtime margin predicate for each grid threshold.
        row["evidence_status"] = "distant_region_margin_evidence_available"
        return row
    if not candidate_counts_available:
        row["evidence_status"] = "candidate_outcome_counts_unavailable"
        return row
    if not isinstance(counts, dict):
        row["evidence_status"] = "candidate_outcome_counts_malformed"
        return row
    if counts["scored"] == 0:
        row["evidence_status"] = "scored_candidate_count_zero"
        return row
    if evidence.get("mode_source") != "refined_local_modes":
        row["evidence_status"] = "refined_local_modes_missing"
        return row
    modes = evidence.get("mode_evidence")
    if not isinstance(modes, list):
        row["evidence_status"] = "mode_evidence_not_a_list"
        return row
    if type(evidence.get("top_mode_converged")) is not bool:
        row["evidence_status"] = "top_mode_convergence_missing"
        return row
    if (
        not evidence["top_mode_converged"]
        or top_east is None
        or top_north is None
        or top_score is None
    ):
        row["evidence_status"] = "top_mode_not_converged"
        return row
    mode_index = evidence.get("top_mode_index")
    if type(mode_index) is not int or not 0 <= mode_index < len(modes):
        row["evidence_status"] = "top_mode_index_invalid"
        return row
    if any(
        not isinstance(mode, dict)
        or mode.get("converged") is not True
        or mode.get("ellipse_rank") != 2
        for mode in modes
    ):
        row["evidence_status"] = "mode_evidence_not_converged_rank2"
        return row
    top_mode = modes[mode_index]
    if not _matches_top(top_mode, top):
        row["evidence_status"] = "top_mode_not_in_converged_evidence"
        return row
    row["ellipse_rank"] = top_mode["ellipse_rank"]
    row["radius_95_m"] = _number(top_mode.get("radius_95_m"))

    distant_modes = [
        mode
        for mode in modes
        if math.hypot(
            float(mode["east_m"]) - float(top_mode["east_m"]),
            float(mode["north_m"]) - float(top_mode["north_m"]),
        )
        >= 100.0
    ]
    competitor = min(
        distant_modes,
        key=lambda mode: (
            float(mode["score"]),
            float(mode["east_m"]),
            float(mode["north_m"]),
        ),
        default=None,
    )
    if competitor is None:
        row["meaningful_distant_coverage"] = False
        row["evidence_status"] = "no_converged_distant_competitor"
        return row
    mode_gap = float(competitor["score"]) - float(top_mode["score"])
    runtime_tolerance = _score_tolerance(top_score)
    if mode_gap <= runtime_tolerance:
        row["evidence_status"] = "converged_mode_within_score_tolerance"
        return row
    row["meaningful_distant_coverage"] = True
    row["score_gap"] = mode_gap
    row["evidence_status"] = "complete"
    return row


def _manifest_artifact(
    manifest_path: Path, trial: dict[str, Any], path_key: str, hash_key: str
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    relative = trial.get(path_key)
    expected_hash = trial.get(hash_key)
    if not isinstance(relative, str) or not relative:
        return None, None, "artifact_path_missing"
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        return None, None, "artifact_hash_missing"
    base = manifest_path.parent.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        return None, None, "artifact_path_escapes_run"
    if not path.is_file():
        return path, None, "artifact_missing"
    if hfile(path) != expected_hash:
        return path, None, "artifact_hash_mismatch"
    try:
        value = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return path, None, "artifact_json_invalid"
    if value is None:
        return path, None, "artifact_json_invalid"
    return path, value, None


def _manifest_truth(
    input_root: Path, trial: dict[str, Any]
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    raw_path = trial.get("truth_path")
    expected_hash = trial.get("truth_sha256")
    if not isinstance(raw_path, str) or not raw_path or not isinstance(expected_hash, str):
        return None, None, "truth_manifest_reference_missing"
    path = Path(raw_path).resolve()
    if not path.is_relative_to(input_root.resolve()):
        return path, None, "truth_path_outside_frozen_inputs"
    if not path.is_file():
        return path, None, "truth_artifact_missing"
    if hfile(path) != expected_hash:
        return path, None, "truth_artifact_hash_mismatch"
    try:
        value = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return path, None, "truth_artifact_json_invalid"
    if value is None:
        return path, None, "truth_artifact_json_invalid"
    return path, value, None


def _distance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [
        (float(row["error_m"]), float(row["euclidean_error_m"]))
        for row in rows
        if isinstance(row.get("error_m"), (int, float))
        and isinstance(row.get("euclidean_error_m"), (int, float))
    ]

    def summarize(values: list[tuple[float, float]]) -> dict[str, Any]:
        deltas = [abs(euclidean - geodesic) for geodesic, euclidean in values]
        return {
            "pair_count": len(values),
            "mean_absolute_difference_m": sum(deltas) / len(deltas) if deltas else None,
            "max_absolute_difference_m": max(deltas) if deltas else None,
        }

    near_gate = [pair for pair in pairs if 80.0 <= pair[0] <= 120.0 or 80.0 <= pair[1] <= 120.0]
    return {"all_verified_pairs": summarize(pairs), "pairs_near_100m": summarize(near_gate)}


def build_document(
    trial_rows: list[dict[str, Any]],
    run_manifest: dict[str, Any],
    manifest_path: Path,
    input_root: Path,
    split: str,
) -> dict[str, Any]:
    if split not in {"development", "validation"}:
        raise ValueError("Calibration metrics cannot include held-out rows.")
    if run_manifest.get("schema_version") != 1 or run_manifest.get("split") != split:
        raise ValueError("Run manifest schema or split does not match calibration request.")
    entries = run_manifest.get("trials")
    if not isinstance(entries, list):
        raise ValueError("Run manifest trials must be a list.")
    run_index: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("trial_id"), str):
            raise ValueError("Run manifest contains a malformed trial row.")
        trial_id = item["trial_id"]
        if trial_id in run_index:
            raise ValueError(f"Run manifest contains duplicate trial ID {trial_id}.")
        run_index[trial_id] = item
    all_trials: list[dict[str, Any]] = []
    for trial in trial_rows:
        if trial["split"] != split:
            continue
        run_trial = run_index.get(str(trial["trial_id"]))
        estimate_path = None
        estimate = None
        telemetry = None
        evaluation = None
        truth = None
        support = _read_json(folder(input_root, split, trial) / "support.json")
        evaluator_error = evaluator_euclidean = None
        error_source = None
        run_state = {"status": "missing", "failure": "run_manifest_row_missing"}
        if run_trial is not None:
            run_state = {
                "status": str(run_trial.get("status", "missing")),
                "failure": str(run_trial.get("failure", "")),
            }
            estimate_path, estimate, _ = _manifest_artifact(
                manifest_path, run_trial, "estimate_path", "estimate_sha256"
            )
            if estimate_path is not None and estimate is not None:
                telemetry = _read_json(
                    estimate_path.with_name(estimate_path.name + ".telemetry.json")
                )
            _, evaluation, _ = _manifest_artifact(
                manifest_path, run_trial, "evaluation_path", "evaluation_sha256"
            )
            truth_path, truth, _ = _manifest_truth(input_root, run_trial)
            provenance = _manifest_artifact(
                manifest_path,
                run_trial,
                "coordinate_provenance_path",
                "coordinate_provenance_sha256",
            )[1]
            evaluator_estimate = _manifest_artifact(
                manifest_path,
                run_trial,
                "evaluator_estimate_path",
                "evaluator_estimate_sha256",
            )[1]
            raw_estimate_sha = run_trial.get("estimate_sha256")
            truth_sha = run_trial.get("truth_sha256")
            evaluator_sha = run_trial.get("evaluator_estimate_sha256")
            if (
                isinstance(raw_estimate_sha, str)
                and isinstance(truth_sha, str)
                and isinstance(evaluator_sha, str)
            ):
                evaluator_error, evaluator_euclidean, error_source = evaluator_error_metrics(
                    str(trial["trial_id"]),
                    evaluator_estimate,
                    truth,
                    provenance,
                    source_estimate_sha256=raw_estimate_sha,
                    source_truth_sha256=truth_sha if truth_path is not None else None,
                    evaluator_estimate_sha256=evaluator_sha,
                    projected_crs=run_manifest.get("projected_crs"),
                )
        row = build_trial_metrics(
            trial,
            run_state=run_state,
            estimate=estimate,
            telemetry=telemetry,
            evaluation=evaluation,
            truth=truth,
            support=support,
            evaluator_error_m=evaluator_error,
            evaluator_euclidean_error_m=evaluator_euclidean,
            error_source=error_source,
        )
        all_trials.append(row)
    return {
        "schema_version": 1,
        "split": split,
        "run_id": run_manifest.get("run_id"),
        "run_manifest_sha256": hfile(manifest_path),
        "frozen": False,
        "trials": [row for row in all_trials if row["supported"]],
        "all_trials": all_trials,
        "trial_count": len(all_trials),
        "supported_trial_count": sum(row["supported"] for row in all_trials),
        "unsupported_trial_count": sum(not row["supported"] for row in all_trials),
        "evidence_status_counts": _count_status(all_trials),
        "distance_comparison": _distance_summary(all_trials),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _count_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["evidence_status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--split", choices=("development", "validation"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    trials = _read_table(args.plan.parent / plan["trial_table"])
    run_path = args.run_root / args.run_id / "run-manifest.json"
    run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
    document = build_document(trials, run_manifest, run_path, args.input_root, args.split)
    document["plan_sha256"] = hfile(args.plan)
    document["trial_table_sha256"] = plan.get("trial_table_sha256")
    document["run_root"] = str(args.run_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "split": args.split,
                "trial_count": document["trial_count"],
                "supported_trial_count": document["supported_trial_count"],
                "evidence_status_counts": document["evidence_status_counts"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
