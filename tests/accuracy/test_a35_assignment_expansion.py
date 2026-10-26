# ruff: noqa: E402
"""Focused tamper tests for assignment-expansion evaluator evidence."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from maritime_localization_shared.serialization import contract_to_dict
from tools.evaluate_assignment_expansion import (
    _verify_ranked_manifest,
    canonical_hash,
    choose_start_label,
    validate_mode_counts,
    validate_no_unmatched_mode,
    validate_objective_progress,
    verify_event_chain,
    verify_mode_event_correspondence,
)


def _event_record(
    index: int, previous: str, mode_index: int, phase: str, event: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "mode_index": mode_index,
        "phase": phase,
        "event_index": index,
        "event": event,
        "previous_event_sha256": previous,
    }
    payload["event_sha256"] = canonical_hash(payload)
    return payload


def test_event_hash_chain_binds_order_and_payload() -> None:
    first = _event_record(
        0,
        "",
        0,
        "branch_started",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "started"},
    )
    second = _event_record(
        1,
        first["event_sha256"],
        0,
        "branch_result",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "no_improvement"},
    )
    assert verify_event_chain([first, second]) == second["event_sha256"]
    corrupted = [first, {**second, "event": {**second["event"], "status": "improved"}}]
    with pytest.raises(ValueError, match="hash"):
        verify_event_chain(corrupted)


def test_duplicate_branch_event_is_rejected() -> None:
    start = _event_record(
        0,
        "",
        0,
        "branch_started",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "started"},
    )
    result = _event_record(
        1,
        start["event_sha256"],
        0,
        "branch_result",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "no_improvement"},
    )
    duplicate = _event_record(
        2,
        result["event_sha256"],
        0,
        "branch_result",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "no_improvement"},
    )
    row = {
        "branch_event_count": 1,
        "counters": {"visited_branches": 1},
    }
    with pytest.raises(ValueError, match="duplicate"):
        verify_mode_event_correspondence([row], [start, result, duplicate])


def test_missing_branch_result_is_rejected() -> None:
    start = _event_record(
        0,
        "",
        0,
        "branch_started",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "started"},
    )
    row = {"branch_event_count": 1, "counters": {"visited_branches": 1}}
    with pytest.raises(ValueError, match="lacks exactly one result"):
        verify_mode_event_correspondence([row], [start])


def test_ranking_must_cover_all_selected_modes() -> None:
    manifest = {"ranked_selected_modes": []}
    rows: list[dict[str, Any]] = []
    with pytest.raises(ValueError, match="all 64"):
        _verify_ranked_manifest(manifest, rows)


def test_start_choice_is_score_only_with_deterministic_label_tie() -> None:
    def alternative(label: str, score: float) -> dict[str, Any]:
        hypothesis = Hypothesis(
            east_m=1.0,
            north_m=2.0,
            score=score,
            assignments=(Assignment(0, "ais:one"),),
        )
        return {
            "label": label,
            "canonical_hypothesis": json.loads(json.dumps(contract_to_dict(hypothesis))),
        }

    assert choose_start_label([alternative("zeta", 5.0), alternative("alpha", 5.0)]) == "alpha"
    assert choose_start_label([alternative("cache", 5.1), alternative("refined", 5.0)]) == "refined"


def test_seed_start_mismatch_is_detectable() -> None:
    alternatives = [
        {
            "label": "cached_hypothesis",
            "canonical_hypothesis": json.loads(
                json.dumps(contract_to_dict(Hypothesis(0.0, 0.0, 5.0)))
            ),
        },
        {
            "label": "refined_mode.hypothesis",
            "canonical_hypothesis": json.loads(
                json.dumps(contract_to_dict(Hypothesis(1.0, 0.0, 4.0)))
            ),
        },
    ]
    assert choose_start_label(alternatives) == "refined_mode.hypothesis"
    wrongly_recorded = "cached_hypothesis"
    assert wrongly_recorded != choose_start_label(alternatives)


def test_objective_cannot_increase_or_claim_unsupported_improvement() -> None:
    validate_objective_progress(10.0, 9.0, [9.0])
    with pytest.raises(ValueError, match="increased"):
        validate_objective_progress(10.0, 11.0, [])
    with pytest.raises(ValueError, match="lacks an improved branch"):
        validate_objective_progress(10.0, 9.0, [])


def test_unknown_pending_modes_are_rejected() -> None:
    validate_mode_counts({"mode_count": 64, "completed_mode_count": 64, "pending_mode_count": 0})
    with pytest.raises(ValueError, match="sum to 64"):
        validate_mode_counts(
            {"mode_count": 64, "completed_mode_count": 63, "pending_mode_count": 0}
        )
    with pytest.raises(ValueError, match="incomplete"):
        validate_mode_counts(
            {"mode_count": 64, "completed_mode_count": 63, "pending_mode_count": 1}
        )


def test_canonical_event_hash_is_stable_sorted_json() -> None:
    assert (
        canonical_hash({"b": 2, "a": 1})
        == hashlib.sha256(
            json.dumps(
                {"a": 1, "b": 2},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def test_no_unmatched_mode_requires_every_report_and_unchanged_seed() -> None:
    seed = Hypothesis(
        east_m=1.0,
        north_m=2.0,
        score=3.0,
        assignments=(Assignment(0, "ais:one"), Assignment(1, "ais:two")),
    )
    row = {
        "status": "no_unmatched_reports",
        "counters": {},
        "branch_event_count": 0,
        "completed": True,
        "budget_exhausted": False,
    }
    validate_no_unmatched_mode(row, seed, seed, measurement_count=2, result_event_count=0)
    with pytest.raises(ValueError, match="does not cover every report"):
        validate_no_unmatched_mode(
            row,
            Hypothesis(1.0, 2.0, 3.0),
            Hypothesis(1.0, 2.0, 3.0),
            measurement_count=2,
            result_event_count=0,
        )
    with pytest.raises(ValueError, match="changed its exact seed"):
        validate_no_unmatched_mode(
            row,
            seed,
            Hypothesis(2.0, 2.0, 2.9),
            measurement_count=2,
            result_event_count=0,
        )
