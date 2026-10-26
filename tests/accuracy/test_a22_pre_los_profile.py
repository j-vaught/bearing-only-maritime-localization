from __future__ import annotations

import math
from dataclasses import replace

import pytest

from accuracy.test_a21_pre_los import _measurements, _prepared_bundle, _records
from bearing_localization.search.config import SearchConfig
from tools.profile_pre_los_bound import (
    CandidatePoint,
    independently_validate_prunes,
    pre_los_screen,
)


def _scorer(*, config: SearchConfig | None = None):
    measurements = _measurements()
    selected_config = config or SearchConfig(
        max_range_m=1_000.0,
        circular_merge_tolerance_deg=0.5,
        expected_unmatched_reports=1.0,
    )
    prepared = _prepared_bundle(_records(), measurements, selected_config)
    return prepared._scorer, prepared.candidate_xy_m


def test_pre_los_screen_prunes_only_supported_safe_points_and_preserves_exact_tie():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("same-pose-tie", seed_position),
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
        CandidatePoint("offset-200", (seed_position[0] + 200.0, seed_position[1])),
    )

    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    exact = {item.candidate_id: scorer.evaluate(item.position_xy_m) for item in candidates}

    assert [item.candidate_id for item in dispositions] == [
        item.candidate_id for item in candidates
    ]
    assert dispositions[0].action == "exact_incumbent"
    assert dispositions[1].action == "exact_scored"
    assert exact["same-pose-tie"].hypothesis is not None
    assert math.isclose(
        exact["same-pose-tie"].hypothesis.score,
        seed.hypothesis.score,
        abs_tol=1e-8,
        rel_tol=1e-10,
    )
    pruned = [item for item in dispositions if item.action == "proposed_prune"]
    assert pruned
    for item in pruned:
        evaluation = exact[item.candidate_id]
        assert item.preparation_supported is True
        assert item.coverage_certified is True
        assert item.bound_supported is True
        assert item.lower_bound is not None
        assert evaluation.status == "scored" and evaluation.hypothesis is not None
        assert item.lower_bound <= evaluation.hypothesis.score + 1e-8
        assert not math.isclose(
            evaluation.hypothesis.score,
            seed.hypothesis.score,
            abs_tol=1e-8,
            rel_tol=1e-10,
        )


def test_unsupported_pre_los_bound_falls_back_to_exact_score(monkeypatch):
    import tools.profile_pre_los_bound as profile_module

    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None

    class UnsupportedBound:
        bound = None
        supported = False
        reason = "unsupported test"

    monkeypatch.setattr(
        profile_module,
        "pre_los_catalog_row_min_bound",
        lambda *_args, **_kwargs: UnsupportedBound(),
    )
    candidate = CandidatePoint("unsupported-bound", (seed_position[0] + 100.0, seed_position[1]))
    dispositions, _ = pre_los_screen(
        scorer,
        (CandidatePoint("seed", seed_position), candidate),
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )

    visit = dispositions[1]
    assert visit.action == "exact_scored"
    assert visit.bound_supported is False
    assert visit.score is not None
    assert visit.exact_elapsed_s >= 0.0


def test_seed_must_match_exact_score_and_duplicate_ids_fail_closed():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.hypothesis is not None
    one = (CandidatePoint("seed", seed_position),)
    with pytest.raises(ValueError, match="Revalidated incumbent score"):
        pre_los_screen(
            scorer,
            one,
            incumbent_id="seed",
            saved_incumbent_score=seed.hypothesis.score + 1.0,
        )
    with pytest.raises(ValueError, match="unique"):
        pre_los_screen(
            scorer,
            (one[0], one[0]),
            incumbent_id="seed",
            saved_incumbent_score=seed.hypothesis.score,
        )


def test_post_pass_rejects_forged_overlarge_bound():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
    )
    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    pruned = next(item for item in dispositions if item.action == "proposed_prune")
    with pytest.raises(AssertionError, match="bound exceeds exact score"):
        independently_validate_prunes(
            scorer,
            (replace(pruned, lower_bound=1.0e12),),
            exact_best_score=seed.hypothesis.score,
        )


def test_isclose_tie_margin_covers_relative_tolerance_for_negative_best():
    from tools.profile_pre_los_bound import _isclose_tie_margin

    best = -10_000.0
    margin = _isclose_tie_margin(best, abs_tol=1e-8, rel_tol=1e-3)
    candidate_at_edge = best + margin * 0.99
    candidate_beyond_edge = best + margin * 1.01
    assert math.isclose(candidate_at_edge, best, abs_tol=1e-8, rel_tol=1e-3)
    assert not math.isclose(candidate_beyond_edge, best, abs_tol=1e-8, rel_tol=1e-3)
