"""Adversarial tests for the serial/parallel expansion artifact evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
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
    mode_event_counts: dict[int, int] = {}
    output = []
    for index, record in enumerate(records):
        mode = record["mode_index"]
        mode_event_index = record.get("mode_event_index", mode_event_counts.get(mode, 0))
        mode_event_counts[mode] = mode_event_index + 1
        payload = {
            "mode_index": mode,
            "mode_event_index": mode_event_index,
            "phase": record["phase"],
            "event_index": index,
            "event": record["event"],
            "previous_event_sha256": previous,
        }
        digest = evaluator.canonical_hash(payload)
        output.append({**payload, "event_sha256": digest})
        previous = digest
    return output


def _write_events(
    root: Path, records: list[dict[str, Any]], *, checkpoint_prefix: int | None = None
) -> None:
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
    prefix_count = len(events) if checkpoint_prefix is None else checkpoint_prefix
    prefix_lines = (root / evaluator.EVENTS_NAME).read_bytes().splitlines(keepends=True)
    checkpoint["event_count"] = prefix_count
    checkpoint["event_digest"] = events[prefix_count - 1]["event_sha256"] if prefix_count else ""
    checkpoint["events_sha256"] = hashlib.sha256(b"".join(prefix_lines[:prefix_count])).hexdigest()
    _write_json(root / evaluator.CHECKPOINT_NAME, checkpoint)


def _bundle(root: Path, *, parallel: bool) -> Path:
    root.mkdir(parents=True)
    completed = 63 if parallel else 60
    rows = []
    row_modes = [
        mode for mode in (range(64) if parallel else range(60)) if not (parallel and mode == 48)
    ]
    for mode in row_modes:
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
            "package_commit": "2" * 40 if parallel else "1" * 40,
            "harness_commit": "2" * 40 if parallel else "1" * 40,
            "schema": "assignment-expansion-diagnostic-v1",
            "mode_count": 64,
            "completed_mode_count": completed,
            "pending_mode_count": 4 if not parallel else 1,
            "pending_mode_ids": [48] if parallel else None,
            "completed_mode_indices": row_modes if parallel else None,
            "unknown_mode_indices": [48] if parallel else None,
            "status": "failed" if parallel else "interrupted",
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
                "package_commit": "2" * 40,
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
        if not parallel and mode >= completed and mode != 60:
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
    _write_events(root, records, checkpoint_prefix=None if parallel else len(records) - 1)
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
    assert result["parallel_completed_mode_count"] == 63
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
        "missing_binding_hash",
        "mode_event_index",
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
    elif mutation == "source_pin":
        manifest = evaluator.read_json(parallel_copy / "manifest.json")
        manifest["source_pins"]["config_sha256"] = "0" * 64
        _write_json(parallel_copy / "manifest.json", manifest)
    elif mutation == "mode_event_index":
        events = evaluator.read_jsonl(parallel_copy / evaluator.EVENTS_NAME)
        events[1]["mode_event_index"] = 0
        records = [
            {
                "mode_index": item["mode_index"],
                "mode_event_index": item["mode_event_index"],
                "phase": item["phase"],
                "event": item["event"],
            }
            for item in events
        ]
        _write_events(parallel_copy, records)
    else:
        manifest = evaluator.read_json(parallel_copy / "manifest.json")
        manifest.pop("assignment_expansion_sha256")
        for worker in manifest["worker_provenance"]:
            worker.pop("assignment_expansion_sha256")
        _write_json(parallel_copy / "manifest.json", manifest)
    with pytest.raises(evaluator.EvaluationError):
        evaluator.evaluate(serial_copy, parallel_copy)


def test_real_serial_artifact_uses_checkpoint_prefix_and_failure_tail(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    root = repository_root / "reports/experiments/assignment-expansion-diagnostic-v1"
    if not root.is_dir():
        root = _bundle(tmp_path / "serial-fallback", parallel=False)
        events = evaluator.read_jsonl(root / evaluator.EVENTS_NAME)
        events.append(
            {
                "mode_index": 60,
                "phase": "failure",
                "event": {
                    "status": "mode_exception",
                    "exception_type": "TimeoutError",
                    "message": "synthetic fixture failure",
                },
            }
        )
        _write_events(root, events, checkpoint_prefix=len(events) - 1)
    manifest, rows, events = evaluator.validate_manifest(root)
    assert manifest["status"] == "interrupted"
    assert len(rows) == 60
    assert events[-1]["phase"] == "failure"
    assert events[-1]["mode_index"] == 60


def test_complete_audit_requires_all_64_exact_artifact_hashes(
    bundles: tuple[Path, Path],
) -> None:
    serial, parallel = bundles
    parallel_manifest = evaluator.read_json(parallel / "manifest.json")
    audit = {
        "evaluation_status": "complete_audit",
        "branch_rescore_complete": True,
        "evaluation_only": True,
        "truth_used_for_search_or_proposal_selection": False,
        "certificate_scope": "none",
        "mode_count": 64,
        "completed_mode_count": 64,
        "pending_mode_count": 0,
        "parallel_status": "failed",
        "parallel_completed_mode_indices": [*range(48), *range(49, 64)],
        "parallel_pending_mode_indices": [48],
        "composite_selected_mode_status": "complete",
        "composite_mode_sources": {
            str(mode): ("parallel" if mode != 48 else "serial") for mode in range(64)
        },
        "serial_completed_mode_count": 60,
        "serial_pending_mode_count": 4,
        "expansion_event_count": parallel_manifest["event_count"],
        "expansion_event_digest": parallel_manifest["event_digest"],
        "expansion_events_sha256": parallel_manifest["events_sha256"],
        "expansion_rows_sha256": parallel_manifest["rows_sha256"],
        "expansion_manifest_sha256": _sha(parallel / "manifest.json"),
        "mode_evaluations": [{"mode_index": mode} for mode in range(64)],
        "event_mode_counts": {
            str(mode): {
                "started": 2 if mode == 60 else 1,
                "result": 2 if mode == 60 else 1,
                "failure": 0,
            }
            for mode in range(64)
        },
    }
    audit_path = parallel.parent / "full-audit.json"
    _write_json(audit_path, audit)
    assert evaluator.evaluate(serial, parallel, audit_path)["evaluation_status"] == "complete_audit"

    audit["expansion_events_sha256"] = "0" * 64
    _write_json(audit_path, audit)
    result = evaluator.evaluate(serial, parallel, audit_path)
    assert result["evaluation_status"] == "provisional"
    assert result["branch_rescore_complete"] is False

    audit["expansion_events_sha256"] = parallel_manifest["events_sha256"]
    audit["event_mode_counts"]["60"]["result"] = 1
    _write_json(audit_path, audit)
    result = evaluator.evaluate(serial, parallel, audit_path)
    assert result["evaluation_status"] == "provisional"

    audit["event_mode_counts"]["60"]["result"] = 2
    audit["mode_evaluations"].pop()
    _write_json(audit_path, audit)
    result = evaluator.evaluate(serial, parallel, audit_path)
    assert result["evaluation_status"] == "provisional"


@pytest.mark.parametrize("field", ("mode_count", "completed_mode_count"))
def test_audit_missing_or_extra_mode_is_not_reusable(
    bundles: tuple[Path, Path], field: str
) -> None:
    serial, parallel = bundles
    manifest = evaluator.read_json(parallel / "manifest.json")
    audit = {
        "evaluation_status": "complete_audit",
        "branch_rescore_complete": True,
        "mode_count": 64,
        "completed_mode_count": 64,
        "assignment_expansion_sha256": manifest["assignment_expansion_sha256"],
        "expansion_events_sha256": manifest["events_sha256"],
        "expansion_rows_sha256": manifest["rows_sha256"],
        "expansion_manifest_sha256": _sha(parallel / "manifest.json"),
    }
    audit[field] = 65 if field == "mode_count" else 63
    path = parallel.parent / f"audit-{field}.json"
    _write_json(path, audit)
    result = evaluator.evaluate(serial, parallel, path)
    assert result["evaluation_status"] == "provisional"


def test_changed_package_commit_is_rejected_even_when_source_hash_is_unchanged(
    bundles: tuple[Path, Path], tmp_path: Path
) -> None:
    serial, parallel = bundles
    copy = tmp_path / "parallel-copy"
    shutil.copytree(parallel, copy)
    manifest = evaluator.read_json(copy / "manifest.json")
    manifest["package_commit"] = "3" * 40
    manifest["harness_commit"] = "3" * 40
    _write_json(copy / "manifest.json", manifest)
    with pytest.raises(evaluator.EvaluationError, match="immutable package commit|worker package"):
        evaluator.evaluate(serial, copy)


def test_parallel_producer_helpers_emit_validator_accepted_ledger(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    runner_path = repository_root / "tools/run_parallel_assignment_expansion_diagnostic.py"
    if not runner_path.is_file():
        override = os.environ.get("PARALLEL_PRODUCER_SOURCE")
        runner_path = Path(override) if override else runner_path
    if not runner_path.is_file():
        pytest.fail(f"committed parallel producer is unavailable at {runner_path}")
    sys.path.insert(0, str(runner_path.parents[1]))
    sys.modules.pop("tools", None)
    spec = importlib.util.spec_from_file_location("parallel_producer_fixture", runner_path)
    assert spec is not None and spec.loader is not None
    producer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = producer
    spec.loader.exec_module(producer)

    root = tmp_path / "producer"
    root.mkdir()
    events_path = root / evaluator.EVENTS_NAME
    state = producer.DurableState()
    branch = _branch(0, result=False)
    with events_path.open("w", encoding="utf-8") as stream:
        producer.append_event(stream, state, 0, "branch_started", branch)
        producer.append_event(stream, state, 0, "branch_result", _branch(0, result=True))

    seed = _hypothesis(3.0)
    row = {
        "mode_index": 0,
        "best_hypothesis": seed,
        "seed": seed,
        "counters": {"visited_branches": 1},
        "branch_event_count": 1,
    }
    state.outcomes[0] = row
    manifest = {
        "mode_count": 64,
        "truth_used": False,
        "evaluator_used": False,
        "certificate_scope": "none",
        "status": "in_progress",
    }
    producer._persist_state(root, manifest, state)
    persisted = evaluator.read_json(root / "manifest.json")
    persisted["rows_sha256"] = _sha(root / evaluator.ROWS_NAME)
    persisted["events_sha256"] = _sha(events_path)
    _write_json(root / "manifest.json", persisted)
    validated_manifest, rows, events = evaluator.validate_manifest(root)
    assert validated_manifest["event_count"] == 2
    assert len(rows) == 1
    assert len(events) == 2


def _artifact_sources_are_resolvable(root: Path) -> bool:
    manifest = evaluator.read_json(root / "manifest.json")
    source_values = [
        manifest.get("assignment_expansion_source"),
        manifest.get("package_source"),
    ]
    source_values.extend(
        item.get("assignment_expansion_source")
        for item in manifest.get("worker_provenance", [])
        if isinstance(item, dict)
    )
    source_values.extend(
        item.get("scorer_source")
        for item in manifest.get("worker_provenance", [])
        if isinstance(item, dict)
    )
    return all(not isinstance(value, str) or Path(value).is_file() for value in source_values)


def test_actual_disk_guard_union_is_sparse_and_provisional(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    serial_root = repository_root / "reports/experiments/assignment-expansion-diagnostic-v1"
    parallel_root = (
        repository_root / "reports/experiments/parallel-assignment-expansion-diagnostic-v1"
    )
    raw_artifacts_present = serial_root.is_dir() and parallel_root.is_dir()
    if raw_artifacts_present:
        serial_manifest, serial_rows, _serial_events = evaluator.validate_manifest(serial_root)
        parallel_manifest, parallel_rows, _parallel_events = evaluator.validate_manifest(
            parallel_root
        )
        assert serial_manifest["status"] == "interrupted"
        assert len(serial_rows) == 60
        assert parallel_manifest["status"] == "failed"
        assert len(parallel_rows) == 63
        assert parallel_manifest["completed_mode_indices"] == [
            *range(48),
            *range(49, 64),
        ]
        assert parallel_manifest["unknown_mode_indices"] == [48]
    if (
        not raw_artifacts_present
        or not _artifact_sources_are_resolvable(serial_root)
        or not _artifact_sources_are_resolvable(parallel_root)
    ):
        serial_root, parallel_root = (
            _bundle(tmp_path / "serial", parallel=False),
            _bundle(tmp_path / "parallel", parallel=True),
        )
        # The portable fallback still exercises sparse mode 48 coverage.
    result = evaluator.evaluate(serial_root, parallel_root)
    assert result["evaluation_status"] == "provisional"
    assert result["parallel_completed_mode_indices"] == [
        *range(48),
        *range(49, 64),
    ]
    assert result["parallel_pending_mode_indices"] == [48]
    assert result["composite_selected_mode_status"] == "complete"
    assert result["composite_mode_sources"]["48"] == "serial"
    assert result["composite_mode_sources"]["0"] == "parallel"


def test_missing_source_fixture_is_rejected(tmp_path: Path) -> None:
    serial = _bundle(tmp_path / "serial", parallel=False)
    parallel = _bundle(tmp_path / "parallel", parallel=True)
    manifest = evaluator.read_json(parallel / "manifest.json")
    manifest["assignment_expansion_source"] = str(tmp_path / "missing-assignment-expansion.py")
    manifest["runner_sha256"] = "0" * 64
    _write_json(parallel / "manifest.json", manifest)
    with pytest.raises(evaluator.EvaluationError, match="assignment source is missing"):
        evaluator.evaluate(serial, parallel)

