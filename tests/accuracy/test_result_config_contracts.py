from __future__ import annotations

from typing import cast

import pytest

from bearing_localization.search.config import ObjectiveVersion, SearchConfig
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    ScoreComponents,
    UncertaintyEllipse,
)


def _hypothesis(score: float = 3.0) -> Hypothesis:
    return Hypothesis(
        east_m=10.0,
        north_m=20.0,
        score=score,
        assignments=(Assignment(0, "catalog-1", residual_deg=0.1, cost=1.0),),
        uncertainty=UncertaintyEllipse(3.0, 2.0, 45.0),
        score_components=ScoreComponents(1.0, 1.0, 1.0, 3.0),
    )


def test_accepted_result_selects_ranked_hypothesis_and_retains_alternatives() -> None:
    result = LocalizationResult(
        hypotheses=(_hypothesis(), Hypothesis(30.0, 40.0, 4.0)),
        elapsed_s=0.25,
        decision_status="accepted",
        search_status="completed",
        certificate_scope="finite_candidate_set",
        accepted_index=0,
        report_count=1,
    )

    assert result.hypotheses[0].assignments[0].catalog_key == "catalog-1"
    assert len(result.hypotheses) == 2
    assert result.accepted_index == 0


def test_ambiguous_result_can_retain_single_broad_mode() -> None:
    result = LocalizationResult(
        hypotheses=(
            Hypothesis(
                1.0,
                2.0,
                0.0,
                uncertainty=UncertaintyEllipse(2_000.0, 1_500.0, 5.0),
            ),
        ),
        elapsed_s=0.0,
        decision_status="ambiguous",
        search_status="completed",
    )

    assert len(result.hypotheses) == 1
    assert result.accepted_index is None


def test_empty_completed_and_failed_results_are_valid_no_fix_records() -> None:
    for status in ("completed", "failed"):
        result = LocalizationResult((), 0.0, "no_fix", status)
        assert not result.hypotheses


def test_budget_interruption_is_expressed_as_budget_exhaustion_and_never_accepted() -> None:
    result = LocalizationResult(
        (_hypothesis(),),
        1.0,
        "ambiguous",
        "budget_exhausted",
        budget_limit=25,
        budget_used=25,
    )

    assert result.search_status == "budget_exhausted"
    with pytest.raises(ValueError, match="unless the search completed"):
        LocalizationResult(
            (_hypothesis(),),
            1.0,
            "accepted",
            "budget_exhausted",
            accepted_index=0,
            budget_limit=25,
            budget_used=25,
        )


def test_invalid_assignment_score_uncertainty_and_result_contracts_are_rejected() -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        Assignment(-1, "catalog-1")
    with pytest.raises(ValueError, match="Ellipse radii"):
        UncertaintyEllipse(1.0, 2.0, 0.0)
    with pytest.raises(ValueError, match="report_count"):
        LocalizationResult((_hypothesis(),), 0.0, "ambiguous", "completed", report_count=0)
    with pytest.raises(ValueError, match="sum of the score components"):
        ScoreComponents(1.0, 1.0, 1.0, 9.0)


def test_search_config_defaults_match_experimental_objective_choices() -> None:
    config = SearchConfig()

    assert config.objective_version == "bearing_set_map_v1"
    assert config.generated_sigma_deg == 0.5
    assert config.sigma_floor_deg == 0.05
    assert config.circular_merge_tolerance_deg == 0.1
    assert config.detection_probability == 0.9
    assert config.bearing_outlier_probability == 0.05
    assert config.expected_unmatched_reports == 1.0
    assert config.correct_type_probability == 0.95
    assert config.common_bearing_bias_prior_deg is None
    assert config.range_inclusive
    assert config.uniform_eligible_water_prior


def test_search_config_rejects_invalid_domains_and_unversioned_objectives() -> None:
    with pytest.raises(ValueError, match="objective_version"):
        SearchConfig(objective_version=cast(ObjectiveVersion, "unknown"))
    with pytest.raises(ValueError, match="bounds_xy"):
        SearchConfig(bounds_xy=(1.0, 1.0, 0.0, 1.0))
    with pytest.raises(ValueError, match="candidate_budget"):
        SearchConfig(candidate_budget=0)
    with pytest.raises(ValueError, match="sigma_floor_deg"):
        SearchConfig(sigma_floor_deg=1.0)
