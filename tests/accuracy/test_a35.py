from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Literal

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.calibration import (
    CalibrationContext,
    CalibrationOperatingCondition,
    DecisionCalibration,
    canonical_json,
    execution_protocol_sha256,
    load_frozen_calibration,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.decision import CompetitorCoverageEvidence, decide_localization
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.hierarchical import run_hierarchical_search_v2
from bearing_localization.search.modes import SearchModeEvidence, converged_mode_evidence
from bearing_localization.search.randomized import run_randomized_search_v2
from bearing_localization.search.refinement import RefinedMode, RefinementResult, StopReason
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    UncertaintyEllipse,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .reference_assignment import optimal_assignments

CRS_UTM = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)


def _origin_xy() -> tuple[float, float]:
    east, north = _TO_XY.transform(*_ORIGIN)
    return float(east), float(north)


def _vector_map() -> VectorMap:
    east, north = _origin_xy()
    return VectorMap(
        None,
        crs=CRS_UTM,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )


def _record(key: str, bearing_deg: float, range_m: float) -> CatalogRecordV2:
    longitude, latitude, _ = _GEOD.fwd(*_ORIGIN, bearing_deg, range_m)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type="vessel",
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="integration-test",
        catalog_version="1",
        catalog_id="a35",
    )


def _measurements(*bearings_and_sigmas: tuple[float, float]) -> MeasurementSetV2:
    return MeasurementSetV2(
        tuple(
            MeasurementV2(bearing, "2026-10-25T12:00:00Z", sigma, "vessel")
            for bearing, sigma in bearings_and_sigmas
        )
    )


def test_exact_assignment_and_fixed_candidate_objective_parity() -> None:
    measurements = _measurements((0.0, 0.1), (90.0, 0.5), (180.0, 2.0))
    catalog = _catalog(
        _record("north", 0.0, 1_000.0),
        _record("east", 90.0, 1_500.0),
        _record("south", 180.0, 2_000.0),
    )
    scorer = CandidateScorer(
        measurements,
        catalog,
        _vector_map(),
        SearchConfig(max_range_m=5_000.0, use_types=False),
    )
    position = _origin_xy()
    expected = scorer.evaluate(position)
    assert expected.status == "scored"
    assert expected.costs is not None and expected.hypothesis is not None
    oracle = optimal_assignments(
        expected.costs.matched_costs.tolist(),
        expected.costs.report_clutter_costs.tolist(),
        expected.costs.group_miss_costs.tolist(),
    )
    assert oracle
    oracle_total = expected.costs.baseline_constant + oracle[0].cost
    assert expected.hypothesis.score == pytest.approx(oracle_total, abs=1e-8, rel=1e-10)
    assert len({item.sigma_deg for item in measurements.measurements}) == 3

    runs = (
        run_grid_search_v2(scorer, candidate_positions=[position]),
        run_hierarchical_search_v2(scorer, candidate_positions=[position]),
        run_randomized_search_v2(scorer, candidate_positions=[position]),
    )
    for run in runs:
        assert run.hypotheses[0].score == pytest.approx(oracle_total, abs=1e-8, rel=1e-10)
        assert run.hypotheses[0].score_components == expected.hypothesis.score_components
        assert run.localization.decision_status == "ambiguous"


def test_minimum_range_is_applied_before_angular_grouping() -> None:
    measurements = _measurements((0.0, 0.5))
    catalog = _catalog(
        _record("too-close", 0.0, 5.0),
        _record("eligible", 0.02, 100.0),
    )
    scorer = CandidateScorer(
        measurements,
        catalog,
        _vector_map(),
        SearchConfig(
            min_range_m=20.0,
            max_range_m=5_000.0,
            circular_merge_tolerance_deg=0.1,
        ),
    )

    evaluation = scorer.evaluate(_origin_xy())

    assert evaluation.status == "scored"
    assert evaluation.predicted is not None
    assert len(evaluation.predicted.groups) == 1
    assert evaluation.predicted.groups[0].member_keys == ("eligible",)
    assert evaluation.predicted.groups[0].representative_key == "eligible"
    assert evaluation.hypothesis is not None
    assert evaluation.hypothesis.assignments[0].catalog_key == "eligible"


def test_distant_unsupported_catalog_geometry_does_not_block_local_scoring() -> None:
    longitude, latitude, _ = _GEOD.fwd(*_ORIGIN, 0.0, 15_000.0)
    unsupported = CatalogRecordV2(
        key="distant-unsupported",
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type="vessel",
        metadata=(("vessel_length_m", 40.0),),
    )
    scorer = CandidateScorer(
        _measurements((0.0, 0.5)),
        _catalog(_record("near", 0.0, 1_000.0), unsupported),
        _vector_map(),
        SearchConfig(max_range_m=5_000.0),
    )

    evaluation = scorer.evaluate(_origin_xy())

    assert evaluation.status == "scored"
    assert evaluation.predicted is not None
    assert evaluation.predicted.unresolved_model is False
    assert evaluation.predicted.unresolved_model_keys == ()


def _execution_protocol() -> dict[str, object]:
    return {
        "schema": "accuracy-execution-protocol-v1",
        "timeout_s": 300.0,
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
    condition = CalibrationOperatingCondition(
        range_m=5_000.0,
        use_types=True,
        objective_version="bearing_set_map_v1",
        algorithm="pairwise",
    )
    context = CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    provenance = {
        split: {
            "plan_sha256": "1" * 64,
            "trial_table_sha256": "2" * 64,
            "run_manifest_sha256": "3" * 64,
            "eligibility_manifest_sha256": "4" * 64,
            "metrics_sha256": "5" * 64,
            "execution_protocol_sha256": execution_protocol_sha256(_execution_protocol()),
        }
        for split in ("development", "validation")
    }
    empty_ids_sha256 = hashlib.sha256(canonical_json([])).hexdigest()
    trial_ids_sha256 = hashlib.sha256(canonical_json(["calibration-trial"])).hexdigest()
    population = {
        split: {
            "included_nominal_supported": {
                "count": 1,
                "trial_ids_sha256": trial_ids_sha256,
            },
            "excluded_stress": {"count": 0, "trial_ids_sha256": empty_ids_sha256},
            "excluded_nominal_unsupported": {
                "count": 0,
                "trial_ids_sha256": empty_ids_sha256,
            },
            "all_condition_trials": {
                "count": 1,
                "trial_ids_sha256": trial_ids_sha256,
            },
        }
        for split in ("development", "validation")
    }
    payload = {
        "schema_version": 2,
        "split": "development",
        "status": "PASS",
        "thresholds": {"min_score_gap": gap, "max_95_radius_m": radius},
        "metrics": {
            "availability": 0.95,
            "conditional_accuracy": 0.96,
            "accepted_p95_error_m": 30.0,
        },
        "validation_metrics": {
            "availability": 0.95,
            "conditional_accuracy": 0.96,
            "accepted_p95_error_m": 30.0,
        },
        "validation_confirmed": True,
        "operating_condition": condition.as_dict(),
        "context": context.as_dict(),
        "provenance": provenance,
        "population": population,
        "execution_protocol": _execution_protocol(),
    }
    document = payload | {
        "frozen": True,
        "sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "frozen-v2-calibration.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_frozen_calibration(
            path,
            expected_condition=condition,
            expected_context=context,
            release_acceptance=True,
        )


def _hypothesis(east: float, score: float, *, radius: float = 20.0) -> Hypothesis:
    return Hypothesis(
        east,
        0.0,
        score,
        tuple(Assignment(i, f"feature-{i}") for i in range(3)),
        UncertaintyEllipse(radius, radius / 2.0, 0.0),
    )


def _mode(hypothesis: Hypothesis, stop_reason: str = "displacement_below_0_01m"):
    assert hypothesis.uncertainty is not None
    return SearchModeEvidence(
        east_m=hypothesis.east_m,
        north_m=hypothesis.north_m,
        score=hypothesis.score,
        matched_catalog_keys=tuple(a.catalog_key for a in hypothesis.assignments),
        radius_95_m=hypothesis.uncertainty.major_radius_m,
        stop_reason=stop_reason,
    )


def _result(
    *hypotheses: Hypothesis,
    status: Literal["completed", "budget_exhausted", "failed"] = "completed",
    certificate: Literal["none", "finite_candidate_set", "continuous_objective"] = "none",
    gap: float | None = None,
) -> LocalizationResult:
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


def _coverage(
    *,
    unresolved: int | None = 0,
    complete: bool = False,
    global_exclusion: bool = False,
    outcome_counts_available: bool = True,
    unresolved_regions: int = 0,
    distant_lower_bound: float | None = None,
    distant_bound_scope: str | None = None,
    distant_bound_anchor: tuple[float, float] | None = None,
):
    return CompetitorCoverageEvidence(
        evaluated_candidate_count=10,
        search_extent_m=0.0,
        coverage_complete=complete,
        global_bound_excludes_outside=global_exclusion,
        unresolved_candidate_count=unresolved,
        candidate_outcome_counts_available=outcome_counts_available,
        unresolved_region_count=unresolved_regions,
        distant_region_score_lower_bound=distant_lower_bound,
        distant_region_bound_scope=distant_bound_scope,
        distant_region_bound_top_east_m=(
            None if distant_bound_anchor is None else distant_bound_anchor[0]
        ),
        distant_region_bound_top_north_m=(
            None if distant_bound_anchor is None else distant_bound_anchor[1]
        ),
    )


def test_completed_heuristic_accepts_two_verified_modes_without_global_coverage():
    top, other = _hypothesis(0.0, 0.0), _hypothesis(1000.0, 2.0)
    decision = decide_localization(
        _result(top, other),
        _calibration(),
        coverage=_coverage(complete=False),
        mode_evidence=(_mode(top), _mode(other)),
    )
    assert (decision.decision_status, decision.accepted_index) == ("accepted", 0)
    assert decision.reason == "calibrated_unique_fix_with_verified_modes"


def test_raw_far_sample_cannot_establish_competitor_evidence():
    top, raw_far = _hypothesis(0.0, 0.0), _hypothesis(1000.0, 5.0)
    decision = decide_localization(
        _result(top, raw_far),
        _calibration(),
        coverage=_coverage(),
        mode_evidence=(_mode(top),),
    )
    assert decision.decision_status == "ambiguous"
    assert decision.reason == "no_meaningful_distant_competitor_or_global_exclusion"


def test_raw_far_low_gap_sample_vetoes_verified_competitor_acceptance():
    top = _hypothesis(0.0, 0.0)
    verified = _hypothesis(1000.0, 3.0)
    raw_promising = _hypothesis(2000.0, 1.5)
    hypotheses = tuple(sorted((top, verified, raw_promising), key=lambda h: h.score))
    decision = decide_localization(
        _result(*hypotheses),
        _calibration(),
        coverage=_coverage(),
        mode_evidence=(_mode(top), _mode(verified)),
    )
    assert decision.decision_status == "ambiguous"
    assert decision.reason == "distant_competitor_within_calibrated_score_gap"


def test_exact_raw_tie_vetoes_even_when_calibrated_threshold_is_zero():
    top, tied = _hypothesis(0.0, 0.0), _hypothesis(1000.0, 0.0)
    decision = decide_localization(
        _result(top, tied),
        _calibration(gap=0.0),
        coverage=_coverage(),
        mode_evidence=(_mode(top), _mode(tied)),
    )
    assert decision.reason == "distant_competitor_within_calibrated_score_gap"


def test_unresolved_geometry_prevents_empirical_acceptance():
    top, other = _hypothesis(0.0, 0.0), _hypothesis(1000.0, 3.0)
    decision = decide_localization(
        _result(top, other),
        _calibration(),
        coverage=_coverage(unresolved=1),
        mode_evidence=(_mode(top), _mode(other)),
    )
    assert decision.reason == "unresolved_evaluated_geometry"


def _refined(hypothesis: Hypothesis, stop_reason: StopReason) -> RefinedMode:
    return RefinedMode(
        hypothesis=hypothesis,
        iterations=50 if stop_reason == "iteration_limit" else 3,
        stop_reason=stop_reason,
        improved_hypothesis=False,
        history=(),
        uncertainty_rank=2,
        uncertainty_singular=False,
        uncertainty_reason=None,
    )


def test_mode_extractor_excludes_iteration_limit_and_deduplicates_nearest_first():
    best = _hypothesis(0.0, 0.0)
    duplicate = _hypothesis(10.0, 0.5)
    converged_far = _hypothesis(1000.0, 3.0)
    unfinished = _hypothesis(2000.0, 4.0)
    refinement = RefinementResult(
        modes=(
            _refined(duplicate, "displacement_below_0_01m"),
            _refined(converged_far, "relative_improvement_below_1e-8"),
            _refined(unfinished, "iteration_limit"),
            _refined(best, "displacement_below_0_01m"),
        ),
        elapsed_s=1.0,
        objective_version="test",
    )
    evidence = converged_mode_evidence(refinement)
    assert [(mode.east_m, mode.score) for mode in evidence] == [(0.0, 0.0), (1000.0, 3.0)]
    assert evidence[0].as_dict() == {
        "east_m": 0.0,
        "north_m": 0.0,
        "score": 0.0,
        "matched_catalog_keys": ["feature-0", "feature-1", "feature-2"],
        "radius_95_m": 20.0,
        "stop_reason": "displacement_below_0_01m",
    }


def test_strict_continuous_global_proof_accepts_without_converged_mode_evidence():
    top = _hypothesis(0.0, 0.0)
    result = _result(top, certificate="continuous_objective", gap=0.0)
    decision = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            complete=True,
            global_exclusion=True,
            distant_lower_bound=3.0,
            distant_bound_scope="all_objective_positions_at_least_100m_from_top",
            distant_bound_anchor=(0.0, 0.0),
        ),
        mode_evidence=None,
    )
    assert decision.decision_status == "accepted"
    assert decision.reason == "calibrated_unique_fix_with_global_exclusion"


def test_strict_proof_can_cover_unavailable_candidate_counters_only_with_closed_queue():
    top = _hypothesis(0.0, 0.0)
    result = _result(top, certificate="continuous_objective", gap=0.0)
    coverage = _coverage(
        unresolved=None,
        complete=True,
        global_exclusion=True,
        outcome_counts_available=False,
        distant_lower_bound=3.0,
        distant_bound_scope="all_objective_positions_at_least_100m_from_top",
        distant_bound_anchor=(0.0, 0.0),
    )
    decision = decide_localization(result, _calibration(), coverage=coverage)
    assert decision.decision_status == "accepted"


def test_global_proof_rejects_unresolved_candidate_or_region_evidence():
    top = _hypothesis(0.0, 0.0)
    result = _result(top, certificate="continuous_objective", gap=0.0)
    unresolved_candidate = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            unresolved=1,
            complete=True,
            global_exclusion=True,
        ),
    )
    unresolved_region = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            unresolved=None,
            complete=True,
            global_exclusion=True,
            outcome_counts_available=False,
            unresolved_regions=1,
            distant_lower_bound=3.0,
            distant_bound_scope="all_objective_positions_at_least_100m_from_top",
            distant_bound_anchor=(0.0, 0.0),
        ),
    )
    assert unresolved_candidate.decision_status == "ambiguous"
    assert unresolved_candidate.reason == "unresolved_evaluated_geometry"
    assert unresolved_region.decision_status == "ambiguous"
    assert unresolved_region.reason == "candidate_outcome_counts_unavailable"


def test_uncertified_or_contradictory_global_claims_do_not_accept():
    top = _hypothesis(0.0, 0.0)
    cases = (
        (
            _result(top, certificate="finite_candidate_set", gap=0.0),
            _coverage(complete=True, global_exclusion=True),
        ),
        (
            _result(top, certificate="continuous_objective", gap=0.0),
            _coverage(
                complete=True,
                global_exclusion=True,
                distant_lower_bound=1.0,
                distant_bound_scope="all_objective_positions_at_least_100m_from_top",
                distant_bound_anchor=(0.0, 0.0),
            ),
        ),
        (
            _result(top, certificate="continuous_objective", gap=0.1),
            _coverage(complete=True, global_exclusion=True),
        ),
        (
            _result(top, certificate="continuous_objective", gap=0.0),
            _coverage(complete=False, global_exclusion=True),
        ),
        (
            _result(top, certificate="continuous_objective", gap=0.0),
            _coverage(complete=True, global_exclusion=False),
        ),
        (
            _result(top, certificate="continuous_objective", gap=0.0, status="budget_exhausted"),
            _coverage(complete=True, global_exclusion=True),
        ),
    )
    for result, coverage in cases:
        decision = decide_localization(result, _calibration(), coverage=coverage)
        assert decision.decision_status == "ambiguous"


def test_global_distance_bound_must_match_final_top_anchor():
    top = _hypothesis(0.0, 0.0)
    result = _result(top, certificate="continuous_objective", gap=0.0)
    decision = decide_localization(
        result,
        _calibration(),
        coverage=_coverage(
            complete=True,
            global_exclusion=True,
            distant_lower_bound=3.0,
            distant_bound_scope="all_objective_positions_at_least_100m_from_top",
            distant_bound_anchor=(10.0, 0.0),
        ),
    )
    assert decision.decision_status == "ambiguous"
