"""Focused evaluator checks for assigned-refinement step evidence."""

from __future__ import annotations

from typing import Any

import pytest

from tools.evaluate_assigned_refinement import (
    bearing_information_summary,
    validate_start_evidence,
)
from tools.evaluate_cached_finite_domain import decode_hypothesis


def hypothesis(score: float, east: float, north: float) -> dict[str, Any]:
    return {
        "east_m": east,
        "north_m": north,
        "score": score,
        "assignments": [],
        "score_components": None,
        "uncertainty": None,
    }


def start_record(steps: list[dict[str, Any]], final: dict[str, Any]) -> dict[str, Any]:
    initial = hypothesis(3.0, 1.0, 2.0)
    return {
        "initial_hypothesis": initial,
        "initial_assignments": [],
        "steps": steps,
        "final_hypothesis": final,
        "final_assignments": [],
    }


def test_accepted_exact_step_updates_final_and_ignored_candidate_does_not() -> None:
    initial = hypothesis(3.0, 1.0, 2.0)
    accepted = hypothesis(2.0, 2.0, 3.0)
    unaccepted = hypothesis(1.5, 9.0, 9.0)
    steps = [
        {
            "step": 1,
            "original_hypothesis": initial,
            "proposed_pose": [2.0, 3.0],
            "exact_hypothesis": accepted,
            "status": "improved",
            "numerical_status": "improved",
            "accepted_for_next_step": True,
        },
        {
            "step": 2,
            "original_hypothesis": accepted,
            "proposed_pose": [9.0, 9.0],
            "exact_hypothesis": unaccepted,
            "status": "no_improvement",
            "numerical_status": "no_improvement",
        },
    ]
    start = start_record(steps, accepted)
    initial_decoded, final_decoded = validate_start_evidence(start, decode_hypothesis(initial))
    assert initial_decoded.score == 3.0
    assert final_decoded.score == 2.0


def test_rejects_increased_accepted_score() -> None:
    initial = hypothesis(3.0, 1.0, 2.0)
    worse = hypothesis(3.1, 2.0, 3.0)
    steps = [
        {
            "step": 1,
            "original_hypothesis": initial,
            "proposed_pose": [2.0, 3.0],
            "exact_hypothesis": worse,
            "status": "improved",
            "numerical_status": "improved",
            "accepted_for_next_step": True,
        }
    ]
    with pytest.raises(ValueError, match="does not improve"):
        validate_start_evidence(start_record(steps, worse), decode_hypothesis(initial))


def test_rejects_final_hypothesis_not_derived_from_accepted_steps() -> None:
    initial = hypothesis(3.0, 1.0, 2.0)
    forged = hypothesis(2.0, 7.0, 7.0)
    with pytest.raises(ValueError, match="differs from accepted exact steps"):
        validate_start_evidence(start_record([], forged), decode_hypothesis(initial))


def test_additional_nonparallel_bearing_improves_linearized_geometry() -> None:
    observer = (0.0, 0.0)
    reports = {
        0: (-1000.0, 5000.0),
        1: (-500.0, 2000.0),
        2: (500.0, 5000.0),
        3: (2000.0, 1000.0),
        4: (1000.0, 8000.0),
    }
    four = bearing_information_summary(observer, reports, [0, 1, 2, 4], 0.5)
    five = bearing_information_summary(observer, reports, [0, 1, 2, 3, 4], 0.5)
    assert five["condition_number"] < four["condition_number"]
    assert five["linearized_95_percent_semiaxes_m"][0] < four["linearized_95_percent_semiaxes_m"][0]
