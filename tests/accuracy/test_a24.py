from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from typing import Literal

import pytest

from bearing_localization.search.calibration import (
    DecisionCalibration,
    canonical_json,
    load_frozen_calibration,
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
