"""Independent review checks for candidate pruning proof components.

These tests intentionally use the exhaustive oracle rather than the production
assignment implementation. The feature modules are absent from older branches,
so imports are skipped until candidate pruning is integrated.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

pytest.importorskip("bearing_localization.search.candidate_lower_bound")

from bearing_localization.search.candidate_lower_bound import (  # noqa: E402
    candidate_objective_lower_bound,
)
from bearing_localization.search.config import SearchConfig  # noqa: E402
from bearing_localization.search.likelihood import build_likelihood_costs  # noqa: E402

from .reference_assignment import optimal_assignments  # noqa: E402


@pytest.mark.parametrize(
    (
        "reports",
        "report_types",
        "report_sigmas",
        "predicted",
        "predicted_types",
        "predicted_sigmas",
        "minimum_groups",
        "config",
    ),
    [
        (
            [359.98, 82.0, 219.9],
            ["physical_aid", "vessel", "shoreline"],
            [0.025, 0.3, 1.2],
            [0.01, 82.2, 220.4, 301.0, 15.0],
            ["physical_aid", "vessel", None, "virtual_aid", "shoreline"],
            [0.0, 0.8, 2.0, 0.05, 4.0],
            3,
            SearchConfig(use_types=True),
        ),
        (
            [7.0, 186.0],
            [None, "physical_aid"],
            [0.05, 0.7],
            [7.0, 187.0, 48.0],
            [None, "physical_aid", "vessel"],
            [0.0, 0.0, 8.0],
            1,
            SearchConfig(
                expected_unmatched_reports=1000.0,
                bearing_outlier_probability=0.02,
                detection_probability=0.97,
                use_types=True,
            ),
        ),
    ],
)
def test_candidate_bound_is_below_exhaustive_actual_group_objectives(
    reports,
    report_types,
    report_sigmas,
    predicted,
    predicted_types,
    predicted_sigmas,
    minimum_groups,
    config,
):
    """Check wrapped/heteroscedastic/type cases, negative NLLs, and k > K."""
    bound = candidate_objective_lower_bound(
        reports,
        minimum_groups,
        config,
        visibility_count_certified=True,
        variance_model_supported=True,
        observation_types=report_types,
        observation_sigmas_deg=report_sigmas,
    )
    assert bound.supported and bound.bound is not None

    costs = build_likelihood_costs(
        reports,
        predicted,
        predicted_types,
        config,
        predicted_sigmas,
        observation_types=report_types,
        observation_sigmas_deg=report_sigmas,
    )
    assignments = optimal_assignments(
        cast(Sequence[Sequence[float]], costs.matched_costs.tolist()),
        cast(Sequence[float], costs.report_clutter_costs.tolist()),
        cast(Sequence[float], costs.group_miss_costs.tolist()),
    )
    assert len(assignments) > 0
    best_complete_objective = costs.baseline_constant + assignments[0].cost
    assert bound.bound <= best_complete_objective + 1e-8

    if len(predicted) > minimum_groups:
        assert len(bound.cardinality_bounds) >= len(reports) + 1


def test_candidate_bound_fails_closed_without_explicit_model_certificate():
    result = candidate_objective_lower_bound(
        [0.0], 1, SearchConfig(), visibility_count_certified=True
    )
    assert not result.supported
    assert result.bound is None
