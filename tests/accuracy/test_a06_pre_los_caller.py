from __future__ import annotations

import math

from bearing_localization.search.config import SearchConfig
from tests.accuracy.test_a21_pre_los import _measurements, _prepared_bundle, _records
from tools.profile_pre_los_bound import (
    ABS_TOL,
    REL_TOL,
    CandidatePoint,
    independently_validate_prunes,
    pre_los_screen,
)


def _scorer():
    measurements = _measurements()
    config = SearchConfig(
        max_range_m=1_000.0,
        circular_merge_tolerance_deg=0.5,
        expected_unmatched_reports=1.0,
        absolute_score_tolerance=1e-8,
        relative_score_tolerance=1e-10,
    )
    prepared = _prepared_bundle(_records(), measurements, config)
    return prepared._scorer, prepared.candidate_xy_m


def test_caller_revalidates_seed_and_accounts_for_every_candidate_in_order():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("same-pose", seed_position),
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
        CandidatePoint("offset-200", (seed_position[0] + 200.0, seed_position[1])),
    )

    dispositions, info = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )

    assert [item.candidate_id for item in dispositions] == [
        item.candidate_id for item in candidates
    ]
    assert dispositions[0].action == "exact_incumbent"
    assert dispositions[1].action == "exact_scored"
    assert set(info["exact_results"]) == {
        item.candidate_id for item in dispositions if item.action != "proposed_prune"
    }
    proposed = [item for item in dispositions if item.action == "proposed_prune"]
    assert proposed
    validations, _ = independently_validate_prunes(
        scorer, proposed, exact_best_score=seed.hypothesis.score
    )
    for item in proposed:
        assert item.preparation_supported is True
        assert item.coverage_certified is True
        assert item.bound_supported is True
        assert item.lower_bound is not None
        exact = validations[item.candidate_id]
        assert exact.status == "scored" and exact.hypothesis is not None
        assert item.lower_bound <= exact.hypothesis.score + ABS_TOL
        assert not math.isclose(
            exact.hypothesis.score,
            seed.hypothesis.score,
            abs_tol=ABS_TOL,
            rel_tol=REL_TOL,
        )


def test_isclose_margin_excludes_ties_for_negative_incumbents():
    from tools.profile_pre_los_bound import _isclose_tie_margin

    best = -10_000.0
    margin = _isclose_tie_margin(best, abs_tol=1e-8, rel_tol=1e-3)
    near_tie = best + margin * 0.99
    outside_tie = best + margin * 1.01
    assert math.isclose(near_tie, best, abs_tol=1e-8, rel_tol=1e-3)
    assert not math.isclose(outside_tie, best, abs_tol=1e-8, rel_tol=1e-3)
    assert best + margin > near_tie


def test_unsupported_bound_uses_exact_fallback(monkeypatch):
    import tools.profile_pre_los_bound as caller

    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None

    class Unsupported:
        supported = False
        bound = None
        reason = "unknown objective metadata"

    monkeypatch.setattr(caller, "pre_los_catalog_row_min_bound", lambda *_args: Unsupported())
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("unknown", (seed_position[0] + 100.0, seed_position[1])),
    )
    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    assert dispositions[1].action == "exact_scored"
    assert dispositions[1].bound_supported is False
    assert dispositions[1].score is not None


def test_post_pass_rejects_a_pruned_candidate_in_the_exact_tie_set():
    from dataclasses import replace

    import pytest

    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("offset", (seed_position[0] + 100.0, seed_position[1])),
    )
    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    proposed = next(item for item in dispositions if item.action == "proposed_prune")
    forged_tie = replace(
        proposed, position_xy_m=seed_position, lower_bound=seed.hypothesis.score - 1.0
    )
    with pytest.raises(AssertionError, match="exact-score tie"):
        independently_validate_prunes(scorer, (forged_tie,), exact_best_score=seed.hypothesis.score)
