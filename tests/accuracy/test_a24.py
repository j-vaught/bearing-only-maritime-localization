from __future__ import annotations

import dataclasses
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any, Literal

import pytest

from bearing_localization.search.calibration import (
    CalibrationContext,
    CalibrationOperatingCondition,
    DecisionCalibration,
    canonical_json,
    execution_protocol_sha256,
    implementation_sha256,
    load_frozen_calibration,
    search_execution_sha256,
)
from bearing_localization.search.decision import (
    CompetitorCoverageEvidence,
    decide_localization,
)
from bearing_localization.search.modes import SearchModeEvidence
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    UncertaintyEllipse,
)

calibration_tool = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "tools" / "calibrate_accuracy_decision.py")
)
calibrate = calibration_tool["calibrate"]
measure = calibration_tool["_measure"]


def _execution_protocol(timeout_s: float = 300.0) -> dict[str, Any]:
    return {
        "schema": "accuracy-execution-protocol-v1",
        "timeout_s": timeout_s,
        "timeout_scope": "per_command_subprocess",
        "deadline_clock": "monotonic_wall_clock_from_spawn",
        "timeout_status": "search_timeout",
        "termination_policy": {
            "start_new_session": True,
            "timeout_signal": "SIGTERM",
            "grace_s": 1.0,
            "escalation_signal": "SIGKILL",
            "reap_process_group": True,
        },
    }


def _calibration(gap: float = 2.0, radius: float | None = 100.0) -> DecisionCalibration:
    payload = {"thresholds": {"gap": gap, "radius": radius}}
    return DecisionCalibration(
        min_score_gap=gap,
        max_95_radius_m=radius,
        split="development",
        status="PASS",
        availability=0.95,
        conditional_accuracy=0.96,
        accepted_p95_error_m=30.0,
        artifact_sha256=hashlib.sha256(canonical_json(payload)).hexdigest(),
        verified_frozen_artifact=True,
        schema_version=2,
        operating_condition=CalibrationOperatingCondition(
            1000.0, True, "bearing_set_map_v1", "pairwise"
        ),
        context=CalibrationContext(
            catalog_sha256="a" * 64,
            map_sha256="b" * 64,
            scope_manifest_sha256="c" * 64,
            search_execution_sha256="d" * 64,
            implementation_sha256="e" * 64,
        ),
        provenance={"development": {}, "validation": {}},
        population={"development": {}, "validation": {}},
        release_eligible=True,
        execution_protocol=_execution_protocol(),
    )


def _hypothesis(east: float, score: float, radius: float | None = 50.0, n: int = 3) -> Hypothesis:
    ellipse = None if radius is None else UncertaintyEllipse(radius, radius / 2, 0.0)
    return Hypothesis(
        east,
        0.0,
        score,
        tuple(Assignment(i, f"vessel-{i}") for i in range(n)),
        ellipse,
    )


def _result(
    *hypotheses: Hypothesis,
    status: Literal["completed", "budget_exhausted", "failed"] = "completed",
    certificate: Literal["none", "finite_candidate_set", "continuous_objective"] = "none",
    gap: float | None = None,
):
    return LocalizationResult(
        hypotheses=tuple(hypotheses),
        elapsed_s=0.1,
        decision_status="ambiguous",
        search_status=status,
        certificate_scope=certificate,
        bound_gap=gap,
        budget_limit=10 if status == "budget_exhausted" else None,
        budget_used=10 if status == "budget_exhausted" else None,
    )


def _mode(
    hypothesis: Hypothesis, stop_reason: str = "displacement_below_0_01m"
) -> SearchModeEvidence:
    assert hypothesis.uncertainty is not None
    return SearchModeEvidence(
        east_m=hypothesis.east_m,
        north_m=hypothesis.north_m,
        score=hypothesis.score,
        matched_catalog_keys=tuple(sorted(a.catalog_key for a in hypothesis.assignments)),
        radius_95_m=hypothesis.uncertainty.major_radius_m,
        stop_reason=stop_reason,
    )


def _coverage(
    *,
    evaluated_candidate_count: int = 20,
    search_extent_m: float = 1000.0,
    coverage_complete: bool = True,
    global_bound_excludes_outside: bool = False,
    distant_region_score_lower_bound: float | None = None,
    distant_region_bound_scope: str | None = None,
    distant_region_bound_anchor: tuple[float, float] | None = None,
) -> CompetitorCoverageEvidence:
    return CompetitorCoverageEvidence(
        evaluated_candidate_count=evaluated_candidate_count,
        search_extent_m=search_extent_m,
        coverage_complete=coverage_complete,
        global_bound_excludes_outside=global_bound_excludes_outside,
        distant_region_score_lower_bound=distant_region_score_lower_bound,
        distant_region_bound_scope=distant_region_bound_scope,
        distant_region_bound_top_east_m=(
            None if distant_region_bound_anchor is None else distant_region_bound_anchor[0]
        ),
        distant_region_bound_top_north_m=(
            None if distant_region_bound_anchor is None else distant_region_bound_anchor[1]
        ),
    )


def test_runtime_rejects_non_release_eligible_calibration():
    result = _result(_hypothesis(0, 0), _hypothesis(150, 4))
    calibration = dataclasses.replace(_calibration(), release_eligible=False)
    decision = decide_localization(
        result,
        calibration,
        coverage=_coverage(),
        mode_evidence=(_mode(result.hypotheses[0]), _mode(result.hypotheses[1])),
    )
    assert decision.decision_status == "ambiguous"
    assert decision.reason == "calibration_not_release_eligible"


def test_accepts_distant_competitor_when_gap_and_radius_are_at_thresholds():
    result = _result(_hypothesis(0, 0, 100), _hypothesis(100, 2))
    decision = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(),
        mode_evidence=tuple(_mode(h) for h in result.hypotheses),
    )
    assert (decision.decision_status, decision.accepted_index) == ("accepted", 0)


def test_distant_tie_and_broad_basin_remain_ambiguous():
    tie = _result(_hypothesis(0, 0), _hypothesis(500, 1.99))
    assert decide_localization(
        tie,
        _calibration(),
        coverage=_coverage(),
        mode_evidence=tuple(_mode(h) for h in tie.hypotheses),
    ).reason == ("distant_competitor_within_calibrated_score_gap")
    broad = _result(_hypothesis(0, 0), _hypothesis(50, 0.1))
    assert decide_localization(
        broad, _calibration(), coverage=_coverage(), mode_evidence=(_mode(broad.hypotheses[0]),)
    ).reason == ("no_meaningful_distant_competitor_or_global_exclusion")


def test_missing_or_singular_ellipse_is_ambiguous_even_with_unrestricted_radius():
    result = _result(_hypothesis(0, 0, None), _hypothesis(500, 40))
    decision = decide_localization(result, _calibration(32, None), coverage=_coverage())
    assert decision.reason == "missing_or_singular_uncertainty_ellipse"


def test_missing_calibration_budget_and_minimum_matches_fail_closed():
    competitor = _hypothesis(500, 40)
    result = _result(_hypothesis(0, 0), competitor)
    assert (
        decide_localization(result, None, coverage=_coverage()).reason
        == "missing_frozen_calibration"
    )
    assert (
        decide_localization(
            _result(*result.hypotheses, status="budget_exhausted"),
            _calibration(),
            coverage=_coverage(),
        ).reason
        == "search_budget_exhausted"
    )
    few = _result(_hypothesis(0, 0, n=2), competitor)
    assert decide_localization(few, _calibration(), coverage=_coverage()).reason == (
        "fewer_than_three_distinct_matched_groups"
    )


def test_finite_certificate_is_not_global_and_coverage_flags_need_evidence():
    result = _result(_hypothesis(0, 0), certificate="finite_candidate_set", gap=0.0)
    evidence = _coverage(global_bound_excludes_outside=True)
    assert decide_localization(
        result,
        _calibration(),
        coverage=evidence,
        mode_evidence=(_mode(result.hypotheses[0]),),
    ).reason == ("no_meaningful_distant_competitor_or_global_exclusion")
    assert (
        decide_localization(
            result,
            _calibration(),
            coverage=_coverage(
                evaluated_candidate_count=1,
                search_extent_m=10000,
            ),
        ).reason
        == "incomplete_competitor_coverage"
    )
    assert (
        decide_localization(
            result,
            _calibration(),
            coverage=_coverage(
                search_extent_m=99,
            ),
            mode_evidence=(_mode(result.hypotheses[0]),),
        ).reason
        == "no_meaningful_distant_competitor_or_global_exclusion"
    )


def test_zero_gap_continuous_certificate_can_exclude_outside():
    result = _result(_hypothesis(0, 0), certificate="continuous_objective", gap=0.0)
    decision = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            global_bound_excludes_outside=True,
            distant_region_score_lower_bound=3.0,
            distant_region_bound_scope="all_objective_positions_at_least_100m_from_top",
            distant_region_bound_anchor=(0.0, 0.0),
        ),
        mode_evidence=(_mode(result.hypotheses[0]),),
    )
    assert decision.decision_status == "accepted"
    small_declared_extent = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            search_extent_m=1.0,
            global_bound_excludes_outside=True,
            distant_region_score_lower_bound=3.0,
            distant_region_bound_scope="all_objective_positions_at_least_100m_from_top",
            distant_region_bound_anchor=(0.0, 0.0),
        ),
        mode_evidence=(_mode(result.hypotheses[0]),),
    )
    assert small_declared_extent.decision_status == "accepted"


def test_calibration_grid_pass_and_split_rejection(tmp_path):
    trials = []
    for index in range(100):
        trials.append(
            {
                "split": "development",
                "search_status": "completed",
                "distinct_matched_groups": 4,
                "ellipse_rank": 2,
                "meaningful_distant_coverage": True,
                "score_gap": 8.0,
                "radius_95_m": 50.0,
                "error_m": 20.0 if index < 96 else 300.0,
            }
        )
    development = {"split": "development", "trials": trials}
    validation = {
        "split": "validation",
        "trials": [row | {"split": "validation"} for row in trials],
    }
    artifact = calibrate(development, validation)
    assert artifact["status"] == "PASS"
    assert artifact["validation_confirmed"] is True
    assert artifact["metrics"]["availability"] >= 0.90
    assert artifact["metrics"]["conditional_accuracy"] >= 0.95
    artifact_path = tmp_path / "frozen-calibration.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    frozen = load_frozen_calibration(artifact_path)
    assert frozen.verified_frozen_artifact is True
    assert frozen.release_eligible is False
    with pytest.raises(ValueError, match="cannot establish release acceptance"):
        load_frozen_calibration(artifact_path, release_acceptance=True)
    tampered = artifact | {"status": "FAIL"}
    artifact_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        load_frozen_calibration(artifact_path)
    with pytest.raises(ValueError, match="development data"):
        calibrate({"split": "heldout", "trials": trials})
    with pytest.raises(ValueError, match="validation data"):
        calibrate(development, {"split": "heldout", "trials": trials})
    with pytest.raises(ValueError, match="development-only"):
        calibrate({"split": "development", "trials": [trials[0] | {"split": "test"}]})


def test_calibration_can_fail_availability_without_weakening_accuracy_gate():
    trials = [
        {
            "split": "development",
            "search_status": "completed",
            "distinct_matched_groups": 3,
            "ellipse_rank": 2,
            "meaningful_distant_coverage": True,
            "score_gap": 32,
            "radius_95_m": 25,
            "error_m": 10,
        }
    ]
    trials.extend(trials[0] | {"distinct_matched_groups": 2} for _ in range(9))
    artifact = calibrate({"split": "development", "trials": trials})
    assert artifact["status"] == "FAIL"
    assert artifact["metrics"]["availability"] < 0.90
    assert artifact["metrics"]["conditional_accuracy"] == 1.0


def _global_proof_trial(**updates):
    row = {
        "search_status": "completed",
        "distinct_matched_groups": 3,
        "ellipse_rank": 2,
        "radius_95_m": 25.0,
        "error_m": 10.0,
        "evaluated_candidate_count": 10,
        "candidate_outcome_counts_available": True,
        "unresolved_candidate_count": 0,
        "unresolved_region_count": 0,
        "coverage_complete": True,
        "global_bound_excludes_outside": True,
        "certificate_scope": "continuous_objective",
        "bound_gap": 0.0,
        "candidate_certificate_scope": "continuous_objective",
        "candidate_bound_gap": 0.0,
        "top_score": 0.0,
        "top_east_m": 0.0,
        "top_north_m": 0.0,
        "score_tolerance": 1e-12,
        "distant_region_score_lower_bound": 3.0,
        "distant_region_bound_scope": "all_objective_positions_at_least_100m_from_top",
        "distant_region_bound_top_east_m": 0.0,
        "distant_region_bound_top_north_m": 0.0,
        "global_exclusion_verified": True,
        "raw_distant_hypothesis_veto_evidence": [],
    }
    return row | updates


def test_calibration_grid_uses_margin_aware_global_proof_without_mode_score_gap():
    proof = _global_proof_trial()
    assert measure([proof], 2.0, None)["accepted_trials"] == 1
    assert measure([proof], 4.0, None)["accepted_trials"] == 0
    no_distant_margin = _global_proof_trial(distant_region_score_lower_bound=1.0)
    assert measure([no_distant_margin], 2.0, None)["accepted_trials"] == 0


def test_calibration_raw_tie_veto_and_exact_zero_mode_gap_match_runtime():
    proof = _global_proof_trial(
        raw_distant_hypothesis_veto_evidence=[{"score_gap": 0.0, "score_tolerance": 1e-12}]
    )
    assert measure([proof], 0.0, None)["accepted_trials"] == 0
    mode = {
        "search_status": "completed",
        "distinct_matched_groups": 3,
        "ellipse_rank": 2,
        "meaningful_distant_coverage": True,
        "score_gap": 0.0,
        "score_tolerance": 1e-12,
        "radius_95_m": 25.0,
        "error_m": 10.0,
    }
    assert measure([mode], 0.0, None)["accepted_trials"] == 0


def test_calibration_uses_runtime_serialized_converged_mode_predicate():
    row = {
        "search_status": "completed",
        "distinct_matched_groups": 3,
        "ellipse_rank": 2,
        "radius_95_m": 25.0,
        "error_m": 10.0,
        "evaluated_candidate_count": 10,
        "candidate_outcome_counts_available": True,
        "unresolved_candidate_count": 0,
        "top_east_m": 0.0,
        "top_north_m": 0.0,
        "top_score": 0.0,
        "mode_evidence": [
            {
                "east_m": 0.0,
                "north_m": 0.0,
                "score": 0.0,
                "matched_catalog_keys": ["a", "b", "c"],
                "radius_95_m": 25.0,
                "stop_reason": "displacement_below_0_01m",
            },
            {
                "east_m": 1000.0,
                "north_m": 0.0,
                "score": 3.0,
                "matched_catalog_keys": ["d", "e", "f"],
                "radius_95_m": 25.0,
                "stop_reason": "relative_improvement_below_1e-8",
            },
        ],
    }
    assert measure([row], 2.0, None)["accepted_trials"] == 1
    assert measure([row], 3.0, None)["accepted_trials"] == 1
    assert measure([row], 3.0001, None)["accepted_trials"] == 0
    raw_far = row | {"mode_evidence": [row["mode_evidence"][0]]}
    assert measure([raw_far], 0.0, None)["accepted_trials"] == 0
    unfinished = row | {
        "mode_evidence": [
            row["mode_evidence"][0],
            row["mode_evidence"][1] | {"stop_reason": "iteration_limit"},
        ]
    }
    assert measure([unfinished], 0.0, None)["accepted_trials"] == 0


def test_calibration_uses_final_result_certificate_not_candidate_proof():
    candidate_only_proof = _global_proof_trial(
        certificate_scope=None,
        bound_gap=None,
        candidate_certificate_scope="continuous_objective",
        candidate_bound_gap=0.0,
    )
    final_proof = _global_proof_trial(
        candidate_certificate_scope="none",
        candidate_bound_gap=None,
    )
    assert measure([candidate_only_proof], 2.0, None)["accepted_trials"] == 0
    assert measure([final_proof], 2.0, None)["accepted_trials"] == 1

    top = _hypothesis(0.0, 0.0)
    coverage = _coverage(
        global_bound_excludes_outside=True,
        distant_region_score_lower_bound=3.0,
        distant_region_bound_scope="all_objective_positions_at_least_100m_from_top",
        distant_region_bound_anchor=(0.0, 0.0),
    )
    runtime_candidate_only = decide_localization(
        _result(top, certificate="none", gap=None),
        _calibration(),
        coverage=coverage,
    )
    runtime_final_proof = decide_localization(
        _result(top, certificate="continuous_objective", gap=0.0),
        _calibration(),
        coverage=coverage,
    )
    assert runtime_candidate_only.decision_status == "ambiguous"
    assert runtime_final_proof.decision_status == "accepted"


def _v2_calibration_doc(split: str, *, condition=None, context=None):
    condition = condition or CalibrationOperatingCondition(
        1000.0, True, "bearing_set_map_v1", "pairwise"
    )
    context = context or CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    all_trials = []
    for index in range(12):
        label = "nominal" if index < 10 else "stress"
        all_trials.append(
            {
                "split": split,
                "trial_id": f"{split}-trial-{index:03d}",
                "range_m": float(condition.range_m),
                "report_types": condition.use_types,
                "sensor_condition": label,
                "supported": True,
                "execution_protocol_sha256": execution_protocol_sha256(_execution_protocol()),
                "search_status": "completed",
                "distinct_matched_groups": 3,
                "ellipse_rank": 2,
                "meaningful_distant_coverage": True,
                "score_gap": 8.0,
                "radius_95_m": 25.0,
                "error_m": 20.0,
            }
        )
    all_trials.append(
        {
            "split": split,
            "trial_id": f"{split}-unsupported-nominal",
            "range_m": float(condition.range_m),
            "report_types": condition.use_types,
            "sensor_condition": "nominal",
            "supported": False,
            "execution_protocol_sha256": execution_protocol_sha256(_execution_protocol()),
            "search_status": "failed",
            "distinct_matched_groups": 0,
            "ellipse_rank": None,
            "meaningful_distant_coverage": False,
            "score_gap": None,
            "radius_95_m": None,
            "error_m": None,
        }
    )
    return {
        "schema_version": 2,
        "split": split,
        "frozen": False,
        "operating_condition": condition.as_dict(),
        "context": context.as_dict(),
        "provenance": {
            "plan_sha256": "1" * 64,
            "trial_table_sha256": "2" * 64,
            "run_manifest_sha256": ("3" if split == "development" else "4") * 64,
            "eligibility_manifest_sha256": "7" * 64,
            "execution_protocol_sha256": execution_protocol_sha256(_execution_protocol()),
        },
        "execution_protocol": _execution_protocol(),
        "population": {
            key: {
                "count": len(rows),
                "trial_ids_sha256": hashlib.sha256(
                    canonical_json(sorted(str(row["trial_id"]) for row in rows))
                ).hexdigest(),
            }
            for key, rows in {
                "condition_wide_planned": all_trials,
                "nominal_planned": [
                    row for row in all_trials if row["sensor_condition"] == "nominal"
                ],
                "supported_nominal": [
                    row
                    for row in all_trials
                    if row["sensor_condition"] == "nominal" and row["supported"]
                ],
                "unsupported_nominal": [
                    row
                    for row in all_trials
                    if row["sensor_condition"] == "nominal" and not row["supported"]
                ],
            }.items()
        },
        "trials": [row for row in all_trials if row["supported"]],
        "all_trials": all_trials,
        "trial_count": len(all_trials),
    }


def test_v2_calibration_uses_nominal_supported_population_and_binds_context(tmp_path):
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    context = CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    dev = _v2_calibration_doc("development", condition=condition, context=context)
    val = _v2_calibration_doc("validation", condition=condition, context=context)
    artifact = calibrate(
        dev,
        val,
        development_metrics_sha256="5" * 64,
        validation_metrics_sha256="6" * 64,
    )
    assert artifact["schema_version"] == 2
    assert artifact["metrics"]["supported_trials"] == 10
    assert artifact["population"]["development"]["included_nominal_supported"]["count"] == 10
    assert artifact["population"]["development"]["excluded_stress"]["count"] == 2
    assert artifact["population"]["development"]["excluded_nominal_unsupported"]["count"] == 1
    assert artifact["provenance"]["development"]["metrics_sha256"] == "5" * 64
    assert artifact["provenance"]["validation"]["metrics_sha256"] == "6" * 64
    assert artifact["provenance"]["development"]["eligibility_manifest_sha256"] == "7" * 64
    assert artifact["execution_protocol"] == _execution_protocol()
    assert artifact["provenance"]["development"][
        "execution_protocol_sha256"
    ] == execution_protocol_sha256(_execution_protocol())

    path = tmp_path / "v2-calibration.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    frozen = load_frozen_calibration(
        path,
        expected_condition=condition,
        expected_context=context,
        release_acceptance=True,
    )
    assert frozen.schema_version == 2
    assert frozen.release_eligible is True

    for field, value in (
        ("range_m", 2000.0),
        ("use_types", False),
        ("objective_version", "legacy_corrected_v0"),
        ("algorithm", "grid"),
    ):
        expected = condition.as_dict() | {field: value}
        with pytest.raises(ValueError, match="condition or execution context mismatch"):
            load_frozen_calibration(
                path,
                expected_condition=CalibrationOperatingCondition.from_dict(expected),
                expected_context=context,
                release_acceptance=True,
            )
    context_fields = context.as_dict()
    for field in context_fields:
        bad_hash = ("f" if context_fields[field] != "f" * 64 else "0") * 64
        wrong_context = CalibrationContext(**(context_fields | {field: bad_hash}))
        with pytest.raises(ValueError, match="condition or execution context mismatch"):
            load_frozen_calibration(
                path,
                expected_condition=condition,
                expected_context=wrong_context,
                release_acceptance=True,
            )
    with pytest.raises(ValueError, match="expected condition and context"):
        load_frozen_calibration(path, release_acceptance=True)


def test_v2_calibration_rejects_mixed_conditions_and_missing_nominal_labels():
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    dev = _v2_calibration_doc("development", condition=condition)
    val = _v2_calibration_doc("validation", condition=condition)
    mixed_range = [dict(row) for row in dev["all_trials"]]
    mixed_range[0]["range_m"] = 2000.0
    mixed_dev = dev | {
        "all_trials": mixed_range,
        "trials": [row for row in mixed_range if row["supported"]],
    }
    with pytest.raises(ValueError, match="mixes range or type-use"):
        calibrate(
            mixed_dev, val, development_metrics_sha256="5" * 64, validation_metrics_sha256="6" * 64
        )

    omitted_population = [dict(row) for row in dev["all_trials"][:-1]]
    omitted_dev = dev | {
        "all_trials": omitted_population,
        "trials": [row for row in omitted_population if row["supported"]],
    }
    with pytest.raises(ValueError, match="condition_wide_planned rows do not match"):
        calibrate(
            omitted_dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    missing_label = [dict(row) for row in dev["all_trials"]]
    del missing_label[0]["sensor_condition"]
    missing_dev = dev | {
        "all_trials": missing_label,
        "trials": [row for row in missing_label if row["supported"]],
    }
    with pytest.raises(ValueError, match="sensor_condition label"):
        calibrate(
            missing_dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    mixed_condition = _v2_calibration_doc(
        "validation",
        condition=CalibrationOperatingCondition(1000.0, False, "bearing_set_map_v1", "pairwise"),
    )
    for field, value in (
        ("use_types", False),
        ("objective_version", "legacy_corrected_v0"),
        ("algorithm", "grid"),
    ):
        mismatched_condition = CalibrationOperatingCondition.from_dict(
            condition.as_dict() | {field: value}
        )
        mismatch_doc = _v2_calibration_doc("validation", condition=mismatched_condition)
        with pytest.raises(ValueError, match="operating conditions differ"):
            calibrate(
                dev,
                mismatch_doc,
                development_metrics_sha256="5" * 64,
                validation_metrics_sha256="6" * 64,
            )
    with pytest.raises(ValueError, match="operating conditions differ"):
        calibrate(
            dev,
            mixed_condition,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )


def test_v2_population_allows_only_explicit_unresolved_stress_rows():
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    dev = _v2_calibration_doc("development", condition=condition)
    val = _v2_calibration_doc("validation", condition=condition)
    for document in (dev, val):
        stress = next(row for row in document["all_trials"] if row["sensor_condition"] == "stress")
        stress["supported"] = None
        stress["eligibility_status"] = "unresolved"
        stress["eligibility_reason"] = "Observer must lie in known water."
        stress["preparation_status"] = "failed"
        document["trials"] = [row for row in document["all_trials"] if row["supported"] is True]
    artifact = calibrate(
        dev,
        val,
        development_metrics_sha256="5" * 64,
        validation_metrics_sha256="6" * 64,
    )
    assert artifact["population"]["development"]["excluded_stress"]["count"] == 2

    missing_reason = _v2_calibration_doc("development", condition=condition)
    stress = next(
        row for row in missing_reason["all_trials"] if row["sensor_condition"] == "stress"
    )
    stress["supported"] = None
    stress["preparation_status"] = "failed"
    stress["eligibility_status"] = "unresolved"
    missing_reason["trials"] = [
        row for row in missing_reason["all_trials"] if row["supported"] is True
    ]
    with pytest.raises(ValueError, match="failed preparation"):
        calibrate(
            missing_reason,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    unresolved_nominal = _v2_calibration_doc("development", condition=condition)
    nominal = next(
        row for row in unresolved_nominal["all_trials"] if row["sensor_condition"] == "nominal"
    )
    nominal["supported"] = None
    with pytest.raises(ValueError, match="Nominal rows"):
        calibrate(
            unresolved_nominal,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )


def test_v2_calibration_binds_and_validates_formal_execution_protocol():
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    dev = _v2_calibration_doc("development", condition=condition)
    val = _v2_calibration_doc("validation", condition=condition)
    artifact = calibrate(
        dev,
        val,
        development_metrics_sha256="5" * 64,
        validation_metrics_sha256="6" * 64,
    )
    assert artifact["execution_protocol"] == _execution_protocol()
    assert (
        artifact["provenance"]["development"]["execution_protocol_sha256"]
        == (artifact["provenance"]["validation"]["execution_protocol_sha256"])
    )

    val["execution_protocol"] = _execution_protocol(timeout_s=301.0)
    val["provenance"]["execution_protocol_sha256"] = execution_protocol_sha256(
        val["execution_protocol"]
    )
    for row in val["all_trials"]:
        row["execution_protocol_sha256"] = val["provenance"]["execution_protocol_sha256"]
    with pytest.raises(ValueError, match="execution protocols differ"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    for bad_timeout in (True, 0.0, -1.0, float("nan"), float("inf")):
        val = _v2_calibration_doc("validation", condition=condition)
        val["execution_protocol"]["timeout_s"] = bad_timeout
        with pytest.raises(ValueError, match="timeout_s must be finite and positive"):
            calibrate(
                dev,
                val,
                development_metrics_sha256="5" * 64,
                validation_metrics_sha256="6" * 64,
            )

    val = _v2_calibration_doc("validation", condition=condition)
    del val["execution_protocol"]["timeout_s"]
    with pytest.raises(ValueError, match="exactly the v1 protocol fields"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    val = _v2_calibration_doc("validation", condition=condition)
    val["execution_protocol"]["timeout_s"] = 0.0
    val["provenance"]["execution_protocol_sha256"] = (
        execution_protocol_sha256(val["execution_protocol"])
        if val["execution_protocol"]["timeout_s"] > 0
        else "0" * 64
    )
    for row in val["all_trials"]:
        row["execution_protocol_sha256"] = val["provenance"]["execution_protocol_sha256"]
    with pytest.raises(ValueError, match="finite and positive"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    for field, invalid in (
        ("timeout_scope", "whole_pipeline"),
        ("deadline_clock", "process_cpu_time"),
        ("timeout_status", "completed"),
    ):
        val = _v2_calibration_doc("validation", condition=condition)
        val["execution_protocol"][field] = invalid
        with pytest.raises(ValueError, match="Unsupported execution_protocol"):
            calibrate(
                dev,
                val,
                development_metrics_sha256="5" * 64,
                validation_metrics_sha256="6" * 64,
            )

    val = _v2_calibration_doc("validation", condition=condition)
    val["execution_protocol"]["termination_policy"]["reap_process_group"] = False
    with pytest.raises(ValueError, match="process-group termination policy"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    val = _v2_calibration_doc("validation", condition=condition)
    val["provenance"]["execution_protocol_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="provenance execution protocol hash"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )

    val = _v2_calibration_doc("validation", condition=condition)
    val["all_trials"][0]["execution_protocol_sha256"] = "0" * 64
    val["trials"] = [row for row in val["all_trials"] if row["supported"]]
    with pytest.raises(ValueError, match="trial row execution protocol hash"):
        calibrate(
            dev,
            val,
            development_metrics_sha256="5" * 64,
            validation_metrics_sha256="6" * 64,
        )


def test_legacy_v2_without_execution_protocol_is_readable_but_not_release_eligible(tmp_path):
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    context = CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    artifact = calibrate(
        _v2_calibration_doc("development", condition=condition),
        _v2_calibration_doc("validation", condition=condition),
        development_metrics_sha256="5" * 64,
        validation_metrics_sha256="6" * 64,
    )
    artifact.pop("execution_protocol")
    for record in artifact["provenance"].values():
        record.pop("execution_protocol_sha256")
    payload = {key: value for key, value in artifact.items() if key not in {"frozen", "sha256"}}
    artifact["sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path = tmp_path / "legacy-v2.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    frozen = load_frozen_calibration(path, expected_condition=condition, expected_context=context)
    assert frozen.verified_frozen_artifact is True
    assert frozen.release_eligible is False
    with pytest.raises(ValueError, match="formal execution protocol"):
        load_frozen_calibration(
            path,
            expected_condition=condition,
            expected_context=context,
            release_acceptance=True,
        )


def test_frozen_protocol_tampering_fails_outer_and_inner_hash_checks(tmp_path):
    condition = CalibrationOperatingCondition(1000.0, True, "bearing_set_map_v1", "pairwise")
    context = CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    artifact = calibrate(
        _v2_calibration_doc("development", condition=condition, context=context),
        _v2_calibration_doc("validation", condition=condition, context=context),
        development_metrics_sha256="5" * 64,
        validation_metrics_sha256="6" * 64,
    )
    path = tmp_path / "frozen-protocol.json"
    artifact["execution_protocol"]["timeout_s"] = 301.0
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 verification"):
        load_frozen_calibration(
            path,
            expected_condition=condition,
            expected_context=context,
            release_acceptance=True,
        )

    payload = {key: value for key, value in artifact.items() if key not in {"frozen", "sha256"}}
    artifact["sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="protocol hash does not match"):
        load_frozen_calibration(
            path,
            expected_condition=condition,
            expected_context=context,
            release_acceptance=True,
        )


def test_context_fingerprints_bind_seed_policy_sources_and_dependency_lock(tmp_path):
    settings = {
        "algorithm": "pairwise",
        "backend": "cpu",
        "device_count": 1,
        "seed": 1,
        "spacing_m": 50.0,
    }
    fixed_one = search_execution_sha256(settings, seed_policy="fixed")
    fixed_two = search_execution_sha256(settings | {"seed": 2}, seed_policy="fixed")
    variable_one = search_execution_sha256(settings, seed_policy="per_trial_variable")
    variable_two = search_execution_sha256(settings | {"seed": 2}, seed_policy="per_trial_variable")
    assert fixed_one != fixed_two
    assert variable_one == variable_two
    with pytest.raises(ValueError, match="Path/output/measurement"):
        search_execution_sha256(
            settings | {"measurements_path": "/tmp/input.json"},
            seed_policy="fixed",
        )

    for root in (tmp_path / "one", tmp_path / "two"):
        (root / "bearing_localization/search").mkdir(parents=True)
        (root / "maritime_localization_shared").mkdir()
        (root / "bearing_localization/search/core.py").write_text("score = 1\n")
        (root / "maritime_localization_shared/contracts.py").write_text("item = 1\n")
        (root / "bearing_localization/accuracy_cli.py").write_text("cli = 1\n")
        (root / "pyproject.toml").write_text("name='pkg'\n")
        (root / "uv.lock").write_text("lock-version=1\n")
    assert implementation_sha256(tmp_path / "one") == implementation_sha256(tmp_path / "two")
    (tmp_path / "two/uv.lock").write_text("lock-version=2\n")
    assert implementation_sha256(tmp_path / "one") != implementation_sha256(tmp_path / "two")
