"""Plan-complete, hash-bound v2 calibration metrics assembly for A38."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from bearing_localization.search.calibration import (
    CalibrationContext,
    CalibrationOperatingCondition,
    canonical_json,
)
from tools.build_a38_calibration_metrics import (
    _count_status,
    _distance_summary,
    _manifest_artifact,
    _manifest_truth,
    _number,
    _public_evidence,
    build_trial_metrics,
    evaluator_error_metrics,
)
from tools.run_a38_execution import hfile


def _ids_sha256(rows: list[dict[str, Any]]) -> str:
    ids = [row.get("trial_id") for row in rows]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError("Every v2 population row must have a nonempty trial_id.")
    if len(ids) != len(set(ids)):
        raise ValueError("V2 population contains duplicate trial IDs.")
    valid_ids = [value for value in ids if isinstance(value, str)]
    return hashlib.sha256(canonical_json(sorted(valid_ids))).hexdigest()


def _population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"count": len(rows), "trial_ids_sha256": _ids_sha256(rows)}


def _checked_artifact(
    manifest_path: Path, row: dict[str, Any], path_key: str, hash_key: str
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    relative = row.get(path_key)
    expected_hash = row.get(hash_key)
    if not relative and not expected_hash:
        return None, None, "artifact_path_missing"
    if not isinstance(relative, str) or not relative:
        raise ValueError(
            f"Manifest hash has no corresponding {path_key} for {row.get('trial_id')}."
        )
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError(f"Manifest path lacks a SHA-256 for {path_key} on {row.get('trial_id')}.")
    path, value, error = _manifest_artifact(manifest_path, row, path_key, hash_key)
    if error is not None and error != "artifact_missing":
        raise ValueError(f"Invalid {path_key} binding for {row.get('trial_id')}: {error}.")
    return path, value, error


def _run_row_matches(row: dict[str, Any], planned: dict[str, str]) -> None:
    expected: dict[str, Any] = {
        "trial_id": planned["trial_id"],
        "split": planned["split"],
        "port_id": planned["port_id"],
        "placement_id": planned["placement_id"],
        "realization": str(planned["realization"]),
        "range_m": int(planned["range_m"]),
        "report_types": planned["report_types"] == "True",
        "physical_seed": int(planned["physical_seed"]),
        "sensor_condition": planned["sensor_condition"],
    }
    for key, value in expected.items():
        actual = row.get(key)
        if key == "report_types":
            if type(actual) is not bool:
                raise ValueError(f"Run row {planned['trial_id']} has nonboolean report_types.")
        elif key in {"range_m", "physical_seed"}:
            if isinstance(actual, bool) or not isinstance(actual, int):
                raise ValueError(f"Run row {planned['trial_id']} has invalid {key}.")
        elif key == "realization":
            if isinstance(actual, bool) or not isinstance(actual, (str, int)):
                raise ValueError(f"Run row {planned['trial_id']} has invalid realization.")
            actual = str(actual)
        elif not isinstance(actual, str):
            raise ValueError(f"Run row {planned['trial_id']} lacks {key} metadata.")
        if actual != value:
            raise ValueError(
                f"Run row metadata differs from plan for {planned['trial_id']}: {key}."
            )


def _condition_context_key(condition: CalibrationOperatingCondition) -> str:
    range_m = int(condition.range_m)
    if float(range_m) != condition.range_m or range_m not in (1000, 5000, 10000):
        raise ValueError("Formal accuracy gates require range 1000, 5000, or 10000 m.")
    return f"{range_m}m_types{'on' if condition.use_types else 'off'}"


def _validated_condition_contexts(
    run_manifest: dict[str, Any],
    *,
    condition: CalibrationOperatingCondition,
    context: CalibrationContext,
) -> dict[str, dict[str, Any]]:
    bindings = run_manifest.get("condition_contexts")
    if not isinstance(bindings, dict):
        raise ValueError("Run manifest lacks its pre-search condition_contexts map.")
    expected_keys = {
        f"{range_m}m_types{label}" for range_m in (1000, 5000, 10000) for label in ("on", "off")
    }
    if set(bindings) != expected_keys:
        raise ValueError("Run manifest condition_contexts must bind all six formal gates.")
    normalized: dict[str, dict[str, Any]] = {}
    execution_fingerprints: set[str] = set()
    for key, binding in bindings.items():
        if not isinstance(binding, dict) or set(binding) != {
            "calibration_condition",
            "calibration_context",
        }:
            raise ValueError(f"Malformed condition_contexts entry {key}.")
        bound_condition = CalibrationOperatingCondition.from_dict(
            binding.get("calibration_condition")
        )
        bound_context = CalibrationContext.from_dict(binding.get("calibration_context"))
        if bound_context.search_execution_sha256 in execution_fingerprints:
            raise ValueError("Each formal gate must bind its own search execution fingerprint.")
        execution_fingerprints.add(bound_context.search_execution_sha256)
        if key != _condition_context_key(bound_condition):
            raise ValueError(f"Condition context key does not match condition at {key}.")
        if (
            bound_condition.objective_version != condition.objective_version
            or bound_condition.algorithm != condition.algorithm
        ):
            raise ValueError("Condition contexts mix objectives or algorithms.")
        normalized[key] = binding
    requested_key = _condition_context_key(condition)
    requested = normalized[requested_key]
    requested_condition = CalibrationOperatingCondition.from_dict(
        requested["calibration_condition"]
    )
    requested_context = CalibrationContext.from_dict(requested["calibration_context"])
    if (
        requested_condition.as_dict() != condition.as_dict()
        or requested_context.as_dict() != context.as_dict()
    ):
        raise ValueError("Requested condition/context differs from pre-search binding.")
    return normalized


def _validate_run_identity(run_manifest: dict[str, Any]) -> None:
    identity_fields = (
        "plan_sha256",
        "eligibility_plan_sha256",
        "eligibility_manifest_sha256",
        "config_sha256",
        "splits_sha256",
        "trial_table_sha256",
        "condition_contexts",
    )
    identity = {key: run_manifest.get(key) for key in identity_fields}
    expected = hashlib.sha256(
        canonical_json({"schema": "accuracy-formal-run-identity-v1", **identity})
    ).hexdigest()
    if run_manifest.get("run_identity_sha256") != expected:
        raise ValueError("Run identity hash does not match its pre-search bindings.")


def build_condition_document(
    *,
    trial_rows: list[dict[str, str]],
    run_manifest: dict[str, Any],
    manifest_path: Path,
    input_root: Path,
    split: str,
    eligibility_snapshot: Any,
    eligibility_manifest_path: Path,
    condition: CalibrationOperatingCondition,
    context: CalibrationContext,
) -> dict[str, Any]:
    """Build one complete split/range/type document; missing outcomes remain rows."""
    if split not in {"development", "validation"}:
        raise ValueError("Calibration metrics cannot include held-out rows.")
    if run_manifest.get("schema_version") != 1 or run_manifest.get("split") != split:
        raise ValueError("Run manifest schema or split does not match request.")
    if eligibility_snapshot.split != split:
        raise ValueError("Eligibility snapshot split does not match request.")
    condition_contexts = _validated_condition_contexts(
        run_manifest, condition=condition, context=context
    )

    eligibility_sha = hfile(eligibility_manifest_path)
    if run_manifest.get("eligibility_manifest_sha256") != eligibility_sha:
        raise ValueError("Run manifest is not bound to the supplied eligibility manifest.")
    # Preserve A26's plan_sha256 meaning (the plan config file bytes) and bind
    # A42's composite config/splits/table identity under a distinct field.
    for manifest_key, snapshot_value in (
        ("plan_sha256", eligibility_snapshot.config_sha256),
        ("config_sha256", eligibility_snapshot.config_sha256),
        ("splits_sha256", eligibility_snapshot.splits_sha256),
        ("trial_table_sha256", eligibility_snapshot.trial_table_sha256),
        ("eligibility_plan_sha256", eligibility_snapshot.plan_sha256),
    ):
        if run_manifest.get(manifest_key) != snapshot_value:
            raise ValueError(f"Run manifest {manifest_key} differs from verified plan.")
    _validate_run_identity(run_manifest)

    selected = [
        row
        for row in trial_rows
        if row["split"] == split
        and int(row["range_m"]) == int(condition.range_m)
        and (row["report_types"] == "True") is condition.use_types
    ]
    if not selected:
        raise ValueError("No frozen plan rows match this operating condition.")
    plan_rows = [row for row in trial_rows if row["split"] == split]
    plan_by_id = {row["trial_id"]: row for row in plan_rows}
    if len(plan_by_id) != len(plan_rows):
        raise ValueError("Frozen split plan has duplicate trial IDs.")
    eligibility_by_id = {item.trial_id: item for item in eligibility_snapshot.all_trials}
    if len(eligibility_by_id) != len(eligibility_snapshot.all_trials):
        raise ValueError("Frozen eligibility contains duplicate trial IDs.")
    if set(plan_by_id) != set(eligibility_by_id):
        raise ValueError("Plan rows do not exactly match the full verified eligibility population.")
    for trial_id, planned_row in plan_by_id.items():
        eligible_row = eligibility_by_id[trial_id]
        expected_values = {
            "split": planned_row["split"],
            "port_id": planned_row["port_id"],
            "placement_id": planned_row["placement_id"],
            "realization": str(planned_row["realization"]),
            "range_m": int(planned_row["range_m"]),
            "report_types": planned_row["report_types"] == "True",
            "physical_seed": int(planned_row["physical_seed"]),
            "sensor_condition": planned_row["sensor_condition"],
        }
        for key, expected_value in expected_values.items():
            actual_value = getattr(eligible_row, key, None)
            if key == "realization":
                actual_value = str(actual_value)
            if actual_value != expected_value:
                raise ValueError(
                    f"Plan metadata differs from verified eligibility for {trial_id}: {key}."
                )

    run_entries = run_manifest.get("trials")
    if not isinstance(run_entries, list):
        raise ValueError("Run manifest trials must be a list.")
    run_by_id: dict[str, dict[str, Any]] = {}
    for row in run_entries:
        if not isinstance(row, dict) or not isinstance(row.get("trial_id"), str):
            raise ValueError("Run manifest contains a malformed trial row.")
        trial_id = row["trial_id"]
        if trial_id in run_by_id:
            raise ValueError(f"Run manifest has duplicate trial ID {trial_id}.")
        if trial_id not in plan_by_id:
            raise ValueError(f"Run manifest includes a row outside this split plan: {trial_id}.")
        _run_row_matches(row, plan_by_id[trial_id])
        row_key = row.get("condition_context_key")
        if not isinstance(row_key, str) or row_key != _condition_context_key(
            CalibrationOperatingCondition(
                range_m=float(plan_by_id[trial_id]["range_m"]),
                use_types=plan_by_id[trial_id]["report_types"] == "True",
                objective_version=condition.objective_version,
                algorithm=condition.algorithm,
            )
        ):
            raise ValueError(f"Run row condition-context key mismatch for {trial_id}.")
        row_binding = condition_contexts.get(row_key)
        if row_binding is None:
            raise ValueError(f"Run row has no pre-search context binding for {trial_id}.")
        row_binding_sha = hashlib.sha256(canonical_json(row_binding)).hexdigest()
        if row.get("condition_context_sha256") != row_binding_sha:
            raise ValueError(f"Run row condition-context hash mismatch for {trial_id}.")
        if (
            row.get("calibration_condition_sha256")
            != hashlib.sha256(canonical_json(row_binding["calibration_condition"])).hexdigest()
        ):
            raise ValueError(f"Run row condition hash mismatch for {trial_id}.")
        if (
            row.get("calibration_context_sha256")
            != hashlib.sha256(canonical_json(row_binding["calibration_context"])).hexdigest()
        ):
            raise ValueError(f"Run row calibration-context hash mismatch for {trial_id}.")
        eligibility_record = eligibility_by_id[trial_id]
        eligibility_payload = (
            asdict(eligibility_record)
            if is_dataclass(eligibility_record)
            else vars(eligibility_record)
        )
        expected_eligibility_sha = hashlib.sha256(canonical_json(eligibility_payload)).hexdigest()
        if row.get("eligibility_record_sha256") != expected_eligibility_sha:
            raise ValueError(f"Run row eligibility record hash mismatch for {trial_id}.")
        run_by_id[trial_id] = row

    all_trials: list[dict[str, Any]] = []
    for planned in selected:
        trial_id = planned["trial_id"]
        eligibility = eligibility_by_id[trial_id]
        raw_condition = planned["sensor_condition"]
        nominal = raw_condition == "nominal"
        supported = eligibility.supported
        if nominal and type(supported) is not bool:
            raise ValueError(f"Nominal support eligibility is unresolved for {trial_id}.")
        if supported is None and (
            nominal
            or eligibility.status != "unresolved"
            or getattr(eligibility, "preparation_status", None) != "failed"
            or not isinstance(getattr(eligibility, "eligibility_reason", None), str)
            or not eligibility.eligibility_reason.strip()
        ):
            raise ValueError(
                "Unresolved stress eligibility lacks explicit failed-preparation "
                f"evidence for {trial_id}."
            )

        sensor_condition = "nominal" if nominal else "stress"
        metric_trial = dict(planned)
        metric_trial["sensor_condition"] = sensor_condition
        run_row = run_by_id.get(trial_id)
        run_state: dict[str, Any] = {"status": "missing", "failure": "run_manifest_row_missing"}
        estimate = telemetry = evaluation = truth = None
        error_m = euclidean_m = None
        error_source = None
        if run_row is not None:
            run_state = {
                "status": str(run_row.get("status", "missing")),
                "failure": str(run_row.get("failure", "")),
            }
            estimate_path, estimate, _ = _checked_artifact(
                manifest_path, run_row, "estimate_path", "estimate_sha256"
            )
            if estimate_path is not None and estimate is not None:
                _, telemetry, _ = _checked_artifact(
                    manifest_path, run_row, "telemetry_path", "telemetry_sha256"
                )
                evidence = _public_evidence(telemetry)
                if evidence is not None:
                    if evidence.get("calibration_condition") != condition.as_dict():
                        raise ValueError(f"Trial telemetry condition mismatch for {trial_id}.")
                    if evidence.get("calibration_context") != context.as_dict():
                        raise ValueError(f"Trial telemetry context mismatch for {trial_id}.")
                    row_config_sha = run_row.get("search_config_sha256") or run_row.get(
                        "config_sha256"
                    )
                    if evidence.get("search_config_sha256") != row_config_sha:
                        raise ValueError(f"Search config hash mismatch for {trial_id}.")
            _, evaluation, _ = _checked_artifact(
                manifest_path, run_row, "evaluation_path", "evaluation_sha256"
            )
            truth_path, truth, truth_error = _manifest_truth(input_root, run_row)
            if truth_error is not None:
                if truth_error not in {
                    "truth_manifest_reference_missing",
                    "truth_artifact_missing",
                }:
                    raise ValueError(f"Truth binding is invalid for {trial_id}: {truth_error}.")
                error_source = truth_error
            provenance = _checked_artifact(
                manifest_path,
                run_row,
                "coordinate_provenance_path",
                "coordinate_provenance_sha256",
            )[1]
            evaluator_estimate = _checked_artifact(
                manifest_path, run_row, "evaluator_estimate_path", "evaluator_estimate_sha256"
            )[1]
            estimate_sha = run_row.get("estimate_sha256")
            truth_sha = run_row.get("truth_sha256")
            evaluator_sha = run_row.get("evaluator_estimate_sha256")
            if all(isinstance(value, str) for value in (estimate_sha, truth_sha, evaluator_sha)):
                error_m, euclidean_m, error_source = evaluator_error_metrics(
                    trial_id,
                    evaluator_estimate,
                    truth,
                    provenance,
                    source_estimate_sha256=estimate_sha,
                    source_truth_sha256=truth_sha if truth_path is not None else None,
                    evaluator_estimate_sha256=evaluator_sha,
                    projected_crs=run_manifest.get("projected_crs"),
                )

        row = build_trial_metrics(
            metric_trial,
            run_state=run_state,
            estimate=estimate,
            telemetry=telemetry,
            evaluation=evaluation,
            truth=truth,
            support={"supported": supported},
            evaluator_error_m=error_m,
            evaluator_euclidean_error_m=euclidean_m,
            error_source=error_source,
        )
        hypotheses = estimate.get("hypotheses") if isinstance(estimate, dict) else None
        top = (
            hypotheses[0]
            if isinstance(hypotheses, list) and hypotheses and isinstance(hypotheses[0], dict)
            else {}
        )
        row.update(
            {
                "range_m": int(planned["range_m"]),
                "report_types": planned["report_types"] == "True",
                "sensor_condition": sensor_condition,
                "sensor_profile": raw_condition,
                "supported": supported,
                "eligibility_status": getattr(eligibility, "eligibility_status", None)
                or (
                    "supported"
                    if supported is True
                    else "unsupported"
                    if supported is False
                    else "unresolved"
                ),
                "eligibility_reason": getattr(eligibility, "eligibility_reason", None),
                "preparation_status": getattr(eligibility, "preparation_status", None),
                "preparation_reason": getattr(eligibility, "preparation_reason", None),
                "support_sha256": getattr(eligibility, "support_sha256", None),
                "support_count": getattr(eligibility, "support_count", None),
                "objective_version": condition.objective_version,
                "algorithm": condition.algorithm,
                "public_evidence": _public_evidence(telemetry),
                "top_east_m": _number(top.get("east_m")),
                "top_north_m": _number(top.get("north_m")),
                "top_score": _number(top.get("score")),
            }
        )
        if isinstance(row.get("public_evidence"), dict):
            row["source_public_evidence"] = row["public_evidence"]
            normalized_evidence = dict(row["public_evidence"])
            normalized_evidence["raw_distant_hypothesis_veto_evidence"] = row[
                "raw_distant_hypothesis_veto_evidence"
            ]
            row["public_evidence"] = normalized_evidence
        all_trials.append(row)

    nominal_rows = [row for row in all_trials if row["sensor_condition"] == "nominal"]
    supported_nominal = [row for row in nominal_rows if row["supported"] is True]
    unsupported_nominal = [row for row in nominal_rows if row["supported"] is False]
    if len(nominal_rows) != len(supported_nominal) + len(unsupported_nominal):
        raise ValueError(
            "Nominal support population does not partition into supported and unsupported."
        )
    return {
        "schema_version": 2,
        "split": split,
        "run_id": run_manifest.get("run_id"),
        "frozen": False,
        "operating_condition": condition.as_dict(),
        "context": context.as_dict(),
        "provenance": {
            "plan_sha256": eligibility_snapshot.plan_sha256,
            "trial_table_sha256": eligibility_snapshot.trial_table_sha256,
            "run_manifest_sha256": hfile(manifest_path),
            "eligibility_manifest_sha256": eligibility_sha,
        },
        "population": {
            "condition_wide_planned": _population(all_trials),
            "nominal_planned": _population(nominal_rows),
            "supported_nominal": _population(supported_nominal),
            "unsupported_nominal": _population(unsupported_nominal),
        },
        "all_trials": all_trials,
        "trials": [row for row in all_trials if row["supported"] is True],
        "trial_count": len(all_trials),
        "supported_trial_count": sum(row["supported"] is True for row in all_trials),
        "evidence_status_counts": _count_status(all_trials),
        "distance_comparison": _distance_summary(all_trials),
    }
