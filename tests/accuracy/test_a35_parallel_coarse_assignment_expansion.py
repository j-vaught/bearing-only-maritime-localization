from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from tools.evaluate_parallel_assignment_expansion import EvaluationError
from tools.evaluate_parallel_coarse_assignment_expansion import (
    COARSE_LABEL,
    verify_coarse_selection,
)


def _hyp(score: float, east: float = 1.0) -> dict[str, Any]:
    return {
        "east_m": east,
        "north_m": 2.0,
        "score": score,
        "assignments": [],
        "score_components": {},
    }


def _fixtures() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    a20_hyp = _hyp(3.0)
    cached = _hyp(2.5)
    refined = _hyp(2.0)
    a20 = [{"input_index": 17, "candidate_id": "cand-17", "cached_hypothesis": a20_hyp}]
    assigned = [
        {
            "input_index": 17,
            "candidate_id": "cand-17",
            "starts": [
                {"label": "cached_hypothesis", "final_hypothesis": cached},
                {"label": "refined_mode.hypothesis", "final_hypothesis": refined},
            ],
        }
    ]
    alternatives = [
        {
            "label": "cached_hypothesis",
            "saved_hypothesis": cached,
            "canonical_hypothesis": cached,
            "canonical_match": True,
            "selected": False,
        },
        {
            "label": "refined_mode.hypothesis",
            "saved_hypothesis": refined,
            "canonical_hypothesis": refined,
            "canonical_match": True,
            "selected": False,
        },
        {
            "label": COARSE_LABEL,
            "origin": "A20.cached_hypothesis",
            "saved_hypothesis": a20_hyp,
            "canonical_hypothesis": a20_hyp,
            "canonical_match": True,
            "selected": True,
        },
    ]
    row = {
        "mode_index": 0,
        "input_index": 17,
        "candidate_id": "cand-17",
        "seed_stage": "coarse",
        "selected_start_label": COARSE_LABEL,
        "selected_start_source": "A20.cached_hypothesis",
        "start_alternatives": alternatives,
        "seed": a20_hyp,
        "best_hypothesis": a20_hyp,
    }
    rows, a20_rows, assigned_rows = [], [], []
    for index in range(64):
        row_copy = deepcopy(row)
        row_copy.update(mode_index=index, input_index=17 + index, candidate_id=f"cand-{17 + index}")
        a20_copy = deepcopy(a20[0])
        a20_copy.update(input_index=17 + index, candidate_id=f"cand-{17 + index}")
        assigned_copy = deepcopy(assigned[0])
        assigned_copy.update(input_index=17 + index, candidate_id=f"cand-{17 + index}")
        rows.append(row_copy)
        a20_rows.append(a20_copy)
        assigned_rows.append(assigned_copy)
    return rows, a20_rows, assigned_rows


def test_accepts_original_a20_seed_and_all_three_saved_alternatives() -> None:
    rows, a20, assigned = _fixtures()
    verify_coarse_selection(rows, a20, assigned)


@pytest.mark.parametrize(
    "mutation", ["candidate", "input_index", "coarse_seed", "selected", "missing_alt", "canonical"]
)
def test_rejects_mismatched_or_unverified_coarse_start(mutation: str) -> None:
    rows, a20, assigned = _fixtures()
    rows = deepcopy(rows)
    if mutation == "candidate":
        rows[0]["candidate_id"] = "other"
    elif mutation == "input_index":
        rows[0]["input_index"] = 18
    elif mutation == "coarse_seed":
        rows[0]["start_alternatives"][2]["saved_hypothesis"] = _hyp(9.0)
    elif mutation == "selected":
        rows[0]["selected_start_label"] = "refined_mode.hypothesis"
    elif mutation == "missing_alt":
        rows[0]["start_alternatives"].pop()
    else:
        rows[0]["start_alternatives"][2]["canonical_match"] = False
    with pytest.raises(EvaluationError):
        verify_coarse_selection(rows, a20, assigned)


def test_accepts_dataclass_a20_mode_rows() -> None:
    from dataclasses import dataclass

    rows, a20, assigned = _fixtures()

    @dataclass(frozen=True)
    class A20Row:
        input_index: int
        candidate_id: str
        cached_hypothesis: dict[str, Any]

    typed_a20 = [
        A20Row(row["input_index"], row["candidate_id"], row["cached_hypothesis"]) for row in a20
    ]
    verify_coarse_selection(rows, typed_a20, assigned)
