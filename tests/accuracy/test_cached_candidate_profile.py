from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from tools.profile_cached_candidate_scoring import (
    best_score_and_ties,
    compare_best,
    fixed_sample_indices,
)


def test_fixed_sample_indices_is_cache_ordered_and_stable() -> None:
    indices = fixed_sample_indices(1_457_546)
    assert len(indices) == 255
    assert indices[:128] == tuple(range(128))
    assert indices == tuple(dict.fromkeys(indices))
    assert indices[-1] == 1_457_545


def test_fixed_sample_indices_deduplicates_small_domain() -> None:
    assert fixed_sample_indices(4, prefix=4, evenly_spaced=4) == (0, 1, 2, 3)
    assert fixed_sample_indices(5, prefix=0, evenly_spaced=3) == (0, 2, 4)


def test_fixed_sample_indices_rejects_invalid_total() -> None:
    with pytest.raises(ValueError):
        fixed_sample_indices(0)


def test_compare_best_uses_required_tolerances_and_ties() -> None:
    left = {"best_score": 10.0, "tie_candidate_ids": ["a", "b"]}
    right = {"best_score": 10.0 + 5e-9, "tie_candidate_ids": ["a", "b"]}
    compare_best(left, right)
    with pytest.raises(AssertionError):
        compare_best(left, {"best_score": 10.0, "tie_candidate_ids": ["a"]})


def test_best_score_and_ties_handles_unscored_audits() -> None:
    from bearing_localization.search.parallel_screened import CandidateAudit

    audits = (
        CandidateAudit("a", 0, (0.0, 0.0), "pruned", None, 1.0, True, 1, None),
        CandidateAudit("b", 1, (1.0, 1.0), "exact_scored", 2.0, None, None, None, "scored"),
        CandidateAudit("c", 2, (2.0, 2.0), "exact_scored", 2.0 + 5e-9, None, None, None, "scored"),
    )
    score, ties = best_score_and_ties(audits)
    assert score == 2.0
    assert ties == ("b", "c")
