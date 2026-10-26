"""Adversarial tests for the serial/parallel expansion artifact evaluator."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tools import evaluate_parallel_assignment_expansion as evaluator

ROOT_KEYS = {
    "package_commit": "pkg",
    "harness_commit": "pkg",
    "assignment_expansion_sha256": "a" * 64,
    "frozen_scoring_source_sha": "b" * 40,
    "assigned_manifest_sha256": "c" * 64,
    "a20_manifest_sha256": "d" * 64,
    "a20_rows_sha256": "e" * 64,
    "source_pins": {"attempt": "attempt", "config_sha256": "f" * 64},
    "input_paths": {"measurements": "measurements", "catalog": "catalog", "map": "map"},
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hypothesis(score: float) -> dict[str, Any]:
    return {
        "east_m": 100.0,
        "north_m": 200.0,
        "score": score,
        "assignments": [],
        "score_components": {"total_cost": score},
        "uncertainty": None,
    }


def _branch(branch_id: int, *, result: bool) -> dict[str, Any]:
    value: dict[str, Any] = {
        "branch_id": branch_id,
        "catalog_key": f"catalog:{branch_id}",
        "report_index": branch_id,
        "status": "no_improvement" if result else "started",
        "exact_score": 3.0 if result else None,
    }
    return value


def _chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous = ""
    output = []
    for index, record in enumerate(records):
        payload = {
            "mode_index": record["mode_index"],
            "mode_event_index": record.get("mode_event_index", 0),
            "phase": record["phase"],
            "event_index": index,
            "event": record["event"],
            "previous_event_sha256": previous,
        }
        digest = evaluator.canonical_hash(payload)
        output.append({**payload, "event_sha256": digest})
        previous = digest
    return output


def _write_events(root: Path, records: list[dict[str, Any]]) -> None:
    events = _chain(records)
    (root / evaluator.EVENTS_NAME).write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in events)
    )
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["event_count"] = len(events)
    manifest["event_digest"] = events[-1]["event_sha256"] if events else ""
    manifest["events_sha256"] = _sha(root / evaluator.EVENTS_NAME)
    _write_json(root / "manifest.json", manifest)
    checkpoint = json.loads((root / evaluator.CHECKPOINT_NAME).read_text())
    checkpoint["event_digest"] = manifest["event_digest"]
    checkpoint["events_sha256"] = manifest["events_sha256"]
    _write_json(root / evaluator.CHECKPOINT_NAME, checkpoint)


def _bundle(root: Path, *, parallel: bool) -> Path:
    root.mkdir(parents=True)
    completed = 64 if parallel else 60
    rows = []
    for mode in range(completed):
        branches = 2 if mode == 60 and parallel else 1
        seed = _hypothesis(3.0)
        rows.append(
            {
                "mode_index": mode,
                "input_index": mode,
                "candidate_id": f"cell:{mode}",
                "selected_start_label": "cached_hypothesis",
                "start_alternatives": [
                    {
                        "label": "cached_hypothesis",
                        "canonical_hypothesis": seed,
                        "selected": True,
                    },
                    {
                        "label": "refined_mode.hypothesis",
                        "canonical_hypothesis": _hypothesis(4.0),
                        "selected": False,
                    },
                ],
                "seed": seed,
                "best_hypothesis": seed,
                "status": "completed",
                "completed": True,
                "budget_exhausted": False,
                "counters": {
                    "candidate_branches": branches,
                    "visited_branches": branches,
                    "unvisited_branches": 0,
                },
                "branch_event_count": branches,
                "elapsed_s": 1.0 + mode,
            }
        )
    common: dict[str, Any] = dict(ROOT_KEYS)
    common.update(
        {
            "schema": "assignment-expansion-diagnostic-v1",
            "mode_count": 64,
            "completed_mode_count": completed,
            "pending_mode_count": 0 if parallel else 4,
            "status": "completed" if parallel else "interrupted",
            "truth_used": False,
            "evaluator_used": False,
            "certificate_scope": "none",
            "rows_file": evaluator.ROWS_NAME,
            "events_file": evaluator.EVENTS_NAME,
            "checkpoint_file": evaluator.CHECKPOINT_NAME,
            "source_run_complete": parallel,
            "input_assigned_package_commit": "input",
        }
    )
    if parallel:
        scorer = root / "scoring.py"
        scorer.write_text("scorer\n")
        common["worker_provenance"] = [
            {
                "worker_id": index,
                "pid": index + 1,
                "package_commit": "pkg",
                "assignment_expansion_sha256": ROOT_KEYS["assignment_expansion_sha256"],
                "scorer_source": str(scorer),
                "scorer_sha256": _sha(scorer),
            }
            for index in range(4)
        ]
    rows_path = root / evaluator.ROWS_NAME
    rows_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    common["rows_sha256"] = _sha(rows_path)
    (root / evaluator.EVENTS_NAME).write_text("")
    common["events_sha256"] = _sha(root / evaluator.EVENTS_NAME)
    common["event_count"] = 0
    common["event_digest"] = ""
    _write_json(root / "manifest.json", common)
    _write_json(
        root / evaluator.CHECKPOINT_NAME,
        {
            "rows_sha256": common["rows_sha256"],
            "event_digest": "",
            "events_sha256": common["events_sha256"],
        },
    )
    records: list[dict[str, Any]] = []
    mode_order = (
        list(range(64))
        if not parallel
        else [mode for pair in zip(range(32), range(32, 64)) for mode in pair]
    )
    for mode in mode_order:
        if mode >= completed and not (not parallel and mode == 60):
            continue
        branches = 2 if mode == 60 else 1
        for branch_id in range(branches):
            records.append(
                {
                    "mode_index": mode,
                    "phase": "branch_started",
                    "event": _branch(branch_id, result=False),
                }
            )
            if not (not parallel and mode == 60 and branch_id == 1):
                records.append(
                    {
                        "mode_index": mode,
                        "phase": "branch_result",
                        "event": _branch(branch_id, result=True),
                    }
                )
    _write_events(root, records)
    return root


@pytest.fixture
def bundles(tmp_path: Path) -> tuple[Path, Path]:
    return _bundle(tmp_path / "serial", parallel=False), _bundle(
        tmp_path / "parallel", parallel=True
    )


def test_valid_partial_serial_prefix_and_complete_parallel_bundle(
    bundles: tuple[Path, Path],
) -> None:
    serial, parallel = bundles
    result = evaluator.evaluate(serial, parallel)
    assert result["evaluation_status"] == "provisional"
    assert result["serial_completed_mode_count"] == 60
    assert result["parallel_completed_mode_count"] == 64
    assert result["truth_used"] is False


@pytest.mark.parametrize(
    "mutation",
    (
        "mode_order",
        "missing_event",
        "duplicate_event",
        "worker_hash",
        "payload",
        "serial_prefix",
        "source_pin",
    ),
)
def test_corruptions_are_rejected(
    bundles: tuple[Path, Path], tmp_path: Path, mutation: str
) -> None:
    serial, parallel = bundles
    serial_copy = tmp_path / "serial-copy"
    parallel_copy = tmp_path / "parallel-copy"
    shutil.copytree(serial, serial_copy)
    shutil.copytree(parallel, parallel_copy)
    if mutation == "mode_order":
        rows = evaluator.read_jsonl(parallel_copy / evaluator.ROWS_NAME)
        rows[0]["mode_index"], rows[1]["mode_index"] = 1, 0
        (parallel_copy / evaluator.ROWS_NAME).write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows)
        )
        manifest = evaluator.read_json(parallel_copy / "manifest.json")
        manifest["rows_sha256"] = _sha(parallel_copy / evaluator.ROWS_NAME)
        _write_json(parallel_copy / "manifest.json", manifest)
    elif mutation == "missing_event":
        events = evaluator.read_jsonl(parallel_copy / evaluator.EVENTS_NAME)[:-1]
        _write_events(parallel_copy, events)
    elif mutation == "duplicate_event":
        events = evaluator.read_jsonl(parallel_copy / evaluator.EVENTS_NAME)
        events.insert(2, dict(events[1]))
        _write_events(parallel_copy, events)
    elif mutation == "worker_hash":
        manifest = evaluator.read_json(parallel_copy / "manifest.json")
        manifest["worker_provenance"][0]["scorer_sha256"] = "0" * 64
        _write_json(parallel_copy / "manifest.json", manifest)
    elif mutation == "payload":
        events = evaluator.read_jsonl(parallel_copy / evaluator.EVENTS_NAME)
        next(item for item in events if item["phase"] == "branch_result")["event"][
            "exact_score"
        ] = 99.0
        _write_events(parallel_copy, events)
    elif mutation == "serial_prefix":
        manifest = evaluator.read_json(serial_copy / "manifest.json")
        rows = evaluator.read_jsonl(serial_copy / evaluator.ROWS_NAME)[:-1]
        (serial_copy / evaluator.ROWS_NAME).write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows)
        )
        manifest["completed_mode_count"] = 59
        manifest["pending_mode_count"] = 5
        manifest["rows_sha256"] = _sha(serial_copy / evaluator.ROWS_NAME)
        _write_json(serial_copy / "manifest.json", manifest)
    else:
        manifest = evaluator.read_json(parallel_copy / "manifest.json")
        manifest["source_pins"]["config_sha256"] = "0" * 64
        _write_json(parallel_copy / "manifest.json", manifest)
    with pytest.raises(evaluator.EvaluationError):
        evaluator.evaluate(serial_copy, parallel_copy)
