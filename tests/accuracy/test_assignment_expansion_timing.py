from __future__ import annotations

from typing import Any, cast

import pytest

from bearing_localization.search import assignment_expansion as expansion
from bearing_localization.search.assignment_expansion import (
    ExpansionConfig,
    expand_assignments,
)

from .test_a20 import _xy
from .test_assignment_expansion import (
    _scorer_with_fifth,
    _single_addition_seed,
)


def _assert_zero_timings(result: Any) -> None:
    assert result.stage_timings is not None
    assert result.stage_timings["pool_preparation_wall_s"] == 0.0
    assert result.stage_timings["frozen_target_preparation_wall_s"] == 0.0
    assert result.stage_timings["solver_fit_wall_s"] == 0.0
    assert result.stage_timings["residual_call_count"] == 0
    assert result.stage_timings["residual_wall_s"] == 0.0
    assert result.stage_timings["canonical_scorer_wall_s"] == 0.0


def test_timing_is_opt_in_and_preserves_result_payload() -> None:
    scorer = _scorer_with_fifth(extra_catalog_records=2)
    seed, _ = _single_addition_seed(scorer)
    config = ExpansionConfig(max_branches=2)

    baseline = expand_assignments(scorer, seed, config=config)
    timed = expand_assignments(scorer, seed, config=config, collect_timing=True)

    assert baseline.stage_timings is None
    assert timed.status == baseline.status
    assert timed.completed == baseline.completed
    assert timed.best_hypothesis == baseline.best_hypothesis
    assert timed.counters == baseline.counters
    assert timed.branch_events == baseline.branch_events
    assert timed.stage_timings is not None
    assert timed.stage_timings["pool_preparation_wall_s"] > 0.0
    assert timed.stage_timings["frozen_target_preparation_wall_s"] > 0.0
    assert timed.stage_timings["solver_fit_wall_s"] >= timed.stage_timings["residual_wall_s"]
    assert timed.stage_timings["residual_call_count"] == sum(
        event["nfev"] for event in timed.branch_events
    )
    assert timed.stage_timings["canonical_scorer_wall_s"] >= 0.0


def test_timing_metadata_is_zero_for_no_branch_result() -> None:
    scorer = _scorer_with_fifth(use_types=False)
    seed = scorer.evaluate(_xy()).hypothesis
    assert seed is not None

    baseline = expand_assignments(scorer, seed, config=ExpansionConfig())
    timed = expand_assignments(scorer, seed, config=ExpansionConfig(), collect_timing=True)

    assert baseline.status == timed.status == "no_unmatched_reports"
    assert baseline.counters == timed.counters == {}
    assert baseline.branch_events == timed.branch_events == ()
    assert timed.stage_timings is not None
    _assert_zero_timings(timed)


def test_max_nfev_records_nested_fit_and_residual_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)
    real_least_squares = expansion.least_squares

    def capped(*args: Any, **kwargs: Any) -> Any:
        kwargs["max_nfev"] = 1
        return real_least_squares(*args, **kwargs)

    monkeypatch.setattr(expansion, "least_squares", capped)
    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_nfev=1, max_branches=1),
        collect_timing=True,
    )

    assert result.counters["visited_branches"] == 1
    assert result.branch_events[0]["nfev"] > 0
    assert result.stage_timings is not None
    assert result.stage_timings["residual_call_count"] == result.branch_events[0]["nfev"]
    assert result.stage_timings["solver_fit_wall_s"] >= result.stage_timings["residual_wall_s"]
    assert result.stage_timings["canonical_scorer_wall_s"] == 0.0


def test_invalid_water_branch_still_records_canonical_timer() -> None:
    scorer = _scorer_with_fifth()
    seed, seed_position = _single_addition_seed(scorer)
    original = scorer.evaluate
    calls = 0

    def invalid_after_seed(query: tuple[float, float]) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(query)
        return expansion.CandidateEvaluation(
            "invalid_water",
            None,
            None,
            None,
            "forced timing fixture",
        )

    setattr(scorer, "evaluate", invalid_after_seed)
    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_branches=1),
        collect_timing=True,
    )

    assert seed_position != result.branch_events[0]["proposed_pose"]
    assert result.branch_events[0]["status"] == "invalid_water"
    assert result.counters["canonical_evaluations"] == 1
    assert result.stage_timings is not None
    assert result.stage_timings["canonical_scorer_wall_s"] > 0.0


def test_callback_exceptions_remain_propagating_with_timing() -> None:
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)

    def callback(_event: dict[str, Any]) -> None:
        raise TimeoutError("timing callback")

    with pytest.raises(TimeoutError, match="timing callback"):
        expand_assignments(
            scorer,
            seed,
            config=ExpansionConfig(max_branches=1),
            progress_callback=callback,
            collect_timing=True,
        )


def test_collect_timing_requires_a_boolean() -> None:
    scorer = _scorer_with_fifth()
    seed, _ = _single_addition_seed(scorer)

    with pytest.raises(TypeError, match="collect_timing"):
        expand_assignments(scorer, seed, config=ExpansionConfig(), collect_timing=cast(Any, 1))
