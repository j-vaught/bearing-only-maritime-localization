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
    evaluate_exact_position,
    validate_branch_score_sequence,
    validate_checkpoint_rows,
    validate_completed_prefix,
    validate_mode_counts,
    validate_no_branch_mode,
    validate_no_unmatched_mode,
    validate_objective_progress,
    verify_checkpoint_event_prefix,
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
    validate_mode_counts(
        {
            "status": "completed",
            "mode_count": 64,
            "completed_mode_count": 64,
            "pending_mode_count": 0,
        }
    )
    with pytest.raises(ValueError, match="sum to 64"):
        validate_mode_counts(
            {"mode_count": 64, "completed_mode_count": 63, "pending_mode_count": 0}
        )
    assert validate_mode_counts(
        {
            "schema": "assignment-expansion-diagnostic-v1",
            "status": "interrupted",
            "mode_count": 64,
            "completed_mode_count": 63,
            "pending_mode_count": 1,
        }
    ) == (63, 1)


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


def test_exact_position_cache_does_not_round_coordinates() -> None:
    class Scorer:
        def __init__(self) -> None:
            self.positions: list[tuple[float, float]] = []

        def evaluate(self, position: tuple[float, float]) -> tuple[float, float]:
            self.positions.append(position)
            return position

    scorer = Scorer()
    cache: dict[tuple[float, float], Any] = {}
    first = (1.0, 2.0)
    second = (1.0 + 1e-12, 2.0)
    assert evaluate_exact_position(scorer, cache, first) == first
    assert evaluate_exact_position(scorer, cache, first) == first
    assert evaluate_exact_position(scorer, cache, second) == second
    assert scorer.positions == [first, second]


def test_branch_improvement_events_follow_strict_best_sequence() -> None:
    branches = [
        {"status": "no_improvement", "exact_score": 10.0},
        {"status": "improved", "exact_score": 9.0},
        {"status": "no_improvement", "exact_score": 9.0},
        {"status": "improved", "exact_score": 8.5},
    ]
    assert validate_branch_score_sequence(10.0, branches) == [9.0, 8.5]
    with pytest.raises(ValueError, match="strict best-score decrease"):
        validate_branch_score_sequence(10.0, [{"status": "improved", "exact_score": 10.1}])
    with pytest.raises(ValueError, match="below the current best"):
        validate_branch_score_sequence(
            10.0,
            [
                {"status": "improved", "exact_score": 9.0},
                {"status": "no_improvement", "exact_score": 8.0},
            ],
        )
    with pytest.raises(ValueError, match="has no exact score"):
        validate_branch_score_sequence(10.0, [{"status": "improved", "exact_score": None}])


def test_partial_rows_must_be_exact_canonical_prefix() -> None:
    validate_completed_prefix(
        [{"mode_index": 0}, {"mode_index": 1}, {"mode_index": 2}],
        completed_count=3,
    )
    with pytest.raises(ValueError, match="row count"):
        validate_completed_prefix([{"mode_index": 0}], completed_count=2)
    with pytest.raises(ValueError, match="deterministic mode prefix"):
        validate_completed_prefix([{"mode_index": 0}, {"mode_index": 2}], completed_count=2)


def test_unfinished_tail_events_are_retained_without_completing_branch() -> None:
    completed_row = {
        "mode_index": 0,
        "status": "no_unmatched_reports",
        "counters": {},
        "branch_event_count": 0,
    }
    tail_start = _event_record(
        0,
        "",
        1,
        "branch_started",
        {"branch_id": 0, "report_index": 4, "catalog_key": "ais:tail", "status": "started"},
    )
    counts = verify_mode_event_correspondence([completed_row], [tail_start], partial_mode_index=1)
    assert counts[1] == {"started": 1, "result": 0, "failure": 0}


def test_preflight_failure_preserves_empty_evidence_without_changing_seed() -> None:
    seed = Hypothesis(1.0, 2.0, 3.0)
    row = {
        "status": "invalid_assignment",
        "counters": {},
        "branch_event_count": 0,
        "completed": False,
        "budget_exhausted": False,
    }
    validate_no_branch_mode(row, seed, seed)
    with pytest.raises(ValueError, match="without exploring"):
        validate_no_branch_mode(row, seed, Hypothesis(2.0, 2.0, 2.0))


def test_checkpoint_may_bind_an_exact_prefix_of_final_event_log(tmp_path: Path) -> None:
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
    third = _event_record(
        2,
        second["event_sha256"],
        1,
        "branch_started",
        {"branch_id": 0, "report_index": 2, "catalog_key": "ais:two", "status": "started"},
    )
    lines = [
        json.dumps(item, separators=(",", ":")).encode() + b"\n" for item in (first, second, third)
    ]
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"".join(lines))
    checkpoint = {
        "event_count": 2,
        "events_sha256": hashlib.sha256(b"".join(lines[:2])).hexdigest(),
        "event_digest": second["event_sha256"],
    }
    verify_checkpoint_event_prefix(checkpoint, path, [first, second, third])
    checkpoint["event_digest"] = third["event_sha256"]
    with pytest.raises(ValueError, match="prefix"):
        verify_checkpoint_event_prefix(checkpoint, path, [first, second, third])


def test_checkpoint_prefix_rejects_middle_edit_and_truncated_line(tmp_path: Path) -> None:
    events = [
        _event_record(
            0,
            "",
            0,
            "branch_started",
            {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "started"},
        ),
        _event_record(
            1,
            "",
            0,
            "branch_result",
            {
                "branch_id": 0,
                "report_index": 1,
                "catalog_key": "ais:one",
                "status": "no_improvement",
            },
        ),
    ]
    events[1]["previous_event_sha256"] = events[0]["event_sha256"]
    events[1]["event_sha256"] = canonical_hash(
        {key: value for key, value in events[1].items() if key != "event_sha256"}
    )
    lines = [json.dumps(item, separators=(",", ":")).encode() + b"\n" for item in events]
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"".join(lines))
    checkpoint = {
        "event_count": 2,
        "events_sha256": hashlib.sha256(b"".join(lines)).hexdigest(),
        "event_digest": events[1]["event_sha256"],
    }
    verify_checkpoint_event_prefix(checkpoint, path, events)

    path.write_bytes(lines[0] + lines[1].replace(b"no_improvement", b"improved"))
    with pytest.raises(ValueError, match="file hash"):
        verify_checkpoint_event_prefix(checkpoint, path, events)

    path.write_bytes(lines[0] + lines[1][:-3])
    with pytest.raises(ValueError, match="file hash"):
        verify_checkpoint_event_prefix(checkpoint, path, events)


def test_checkpoint_prefix_rejects_count_past_end_and_rows_hash_mismatch(
    tmp_path: Path,
) -> None:
    event = _event_record(
        0,
        "",
        0,
        "branch_started",
        {"branch_id": 0, "report_index": 1, "catalog_key": "ais:one", "status": "started"},
    )
    path = tmp_path / "events.jsonl"
    raw = json.dumps(event, separators=(",", ":")).encode() + b"\n"
    path.write_bytes(raw)
    checkpoint = {
        "event_count": 2,
        "events_sha256": hashlib.sha256(raw).hexdigest(),
        "event_digest": event["event_sha256"],
    }
    with pytest.raises(ValueError, match="outside"):
        verify_checkpoint_event_prefix(checkpoint, path, [event])
    with pytest.raises(ValueError, match="rows hash"):
        validate_checkpoint_rows({"rows_sha256": "wrong"}, {"rows_sha256": "bound"})
