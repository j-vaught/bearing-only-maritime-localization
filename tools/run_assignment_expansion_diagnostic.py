"""Bounded local assignment expansion diagnostic runner."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

from maritime_localization_shared.contracts.localization import Hypothesis
from maritime_localization_shared.serialization import contract_from_dict
from tools.run_assigned_refinement_diagnostic import (
    DiagnosticInputError,
    build_scorer,
    canonical_lock_path,
    load_a20_bundle,
    read_json,
    sha256,
    validate_mode_source_refs,
    verify_source_pins,
)

MODE_COUNT = 64
FLOOR_BYTES = 40 * 1024**3
MAX_OUTPUT_BYTES = 20 * 1024**2
SCORE_ATOL = 1e-8
SCORE_RTOL = 1e-10
DEFAULT_TIMEOUT_S = 1800.0


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _output_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def check_resources(path: Path) -> int:
    free = shutil.disk_usage(path).free
    if free < FLOOR_BYTES + MAX_OUTPUT_BYTES:
        raise OSError("40 GiB disk reserve leaves less than the output cap")
    if _output_bytes(path) > MAX_OUTPUT_BYTES:
        raise OSError("diagnostic output exceeded 20 MiB")
    return free


def _alarm(_signal: int, _frame: Any) -> None:
    raise TimeoutError("whole diagnostic deadline exceeded")


def _hypothesis(value: Any, field: str) -> Hypothesis:
    if type(value) is not dict:
        raise DiagnosticInputError(f"{field} must be a Hypothesis mapping")
    try:
        decoded = contract_from_dict(Hypothesis, value)
    except (TypeError, ValueError, KeyError) as exc:
        raise DiagnosticInputError(f"{field} is not a valid Hypothesis") from exc
    if not isinstance(decoded, Hypothesis):
        raise DiagnosticInputError(f"{field} did not decode to Hypothesis")
    return decoded


def _same_score(left: float, right: float) -> bool:
    return math.isclose(left, right, abs_tol=SCORE_ATOL, rel_tol=SCORE_RTOL)


def _load_rows(path: Path, expected_hash: str) -> list[dict[str, Any]]:
    if not path.is_file() or sha256(path) != expected_hash:
        raise DiagnosticInputError("assigned rows hash mismatch")
    rows = []
    with path.open() as stream:
        for line_number, line in enumerate(stream):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DiagnosticInputError(f"invalid assigned row at {line_number}") from exc
            if type(value) is not dict:
                raise DiagnosticInputError("assigned rows must contain objects")
            rows.append(value)
    return rows


def load_assigned_bundle(root: Path, a20: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != "assigned-bearing-refinement-diagnostic-v1":
        raise DiagnosticInputError("unexpected assigned artifact schema")
    if manifest.get("status") != "completed":
        raise DiagnosticInputError("assigned artifact is not complete")
    if (
        manifest.get("mode_count") != MODE_COUNT
        or manifest.get("completed_mode_count") != MODE_COUNT
    ):
        raise DiagnosticInputError("assigned artifact does not contain 64 completed modes")
    rows_file = manifest.get("rows_file")
    if (
        type(rows_file) is not str
        or Path(rows_file).name != rows_file
        or "/" in rows_file
        or "\\" in rows_file
    ):
        raise DiagnosticInputError("assigned rows_file must be one safe basename")
    rows = _load_rows(root / rows_file, manifest.get("rows_sha256"))
    if len(rows) != MODE_COUNT:
        raise DiagnosticInputError("assigned rows do not contain exactly 64 modes")
    for index, row in enumerate(rows):
        if row.get("mode_index") != index:
            raise DiagnosticInputError("assigned mode rows are not in order")
        starts = row.get("starts")
        if type(starts) is not list or len(starts) != 2:
            raise DiagnosticInputError("each assigned mode must retain two starts")
        if [item.get("label") for item in starts] != [
            "cached_hypothesis",
            "refined_mode.hypothesis",
        ]:
            raise DiagnosticInputError("assigned start labels are not the frozen pair")
    a20_manifest = read_json(a20 / "manifest.json")
    if manifest.get("a20_manifest_sha256") != sha256(a20 / "manifest.json"):
        raise DiagnosticInputError("A20 manifest hash mismatch")
    if manifest.get("a20_rows_sha256") != a20_manifest.get("rows_sha256"):
        raise DiagnosticInputError("A20 rows hash differs from assigned artifact")
    if manifest.get("source_pins", {}).get("frozen_scoring_source_sha") != a20_manifest.get(
        "package_commit"
    ):
        raise DiagnosticInputError("A20 package source pin mismatch")
    if manifest.get("package_commit") != manifest.get("harness_commit"):
        raise DiagnosticInputError("assigned package and harness pins differ")
    return rows, manifest


def _start_hypotheses(row: dict[str, Any]) -> list[tuple[str, Hypothesis]]:
    return [
        (str(item["label"]), _hypothesis(item.get("final_hypothesis"), "final_hypothesis"))
        for item in row["starts"]
    ]


def choose_best_start(row: dict[str, Any]) -> tuple[str, Hypothesis]:
    return min(_start_hypotheses(row), key=lambda item: (item[1].score, item[0]))


def _revalidate(scorer: Any, source: Hypothesis) -> Hypothesis:
    evaluation = scorer.evaluate((source.east_m, source.north_m))
    if evaluation.status != "scored" or evaluation.hypothesis is None:
        raise DiagnosticInputError(f"saved start is {evaluation.status}")
    exact = evaluation.hypothesis
    if not _same_score(exact.score, source.score) or exact.assignments != source.assignments:
        raise DiagnosticInputError("saved start differs from canonical score or assignments")
    return exact


def _append_event(
    stream: Any, state: dict[str, Any], mode_index: int, phase: str, event: dict[str, Any]
) -> None:
    payload = {
        "mode_index": mode_index,
        "phase": phase,
        "event_index": state["count"],
        "event": event,
        "previous_event_sha256": state["digest"],
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    payload["event_sha256"] = digest
    stream.write(json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
    state["count"] += 1
    state["digest"] = digest


def _checkpoint(
    path: Path,
    status: str,
    completed: list[dict[str, Any]],
    rows_path: Path,
    events_path: Path,
    state: dict[str, Any],
    error: str | None = None,
) -> None:
    value: dict[str, Any] = {
        "schema": "assignment-expansion-checkpoint-v1",
        "status": status,
        "resume_supported": False,
        "completed_mode_count": len(completed),
        "pending_mode_count": MODE_COUNT - len(completed),
        "rows_sha256": sha256(rows_path),
        "events_sha256": sha256(events_path),
        "event_count": state["count"],
        "event_digest": state["digest"],
    }
    if error is not None:
        value["error"] = error
    _write_json(path, value)


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if not math.isfinite(args.timeout_s) or not 0.0 < args.timeout_s <= DEFAULT_TIMEOUT_S:
        raise ValueError("timeout must be finite and in (0, 1800]")
    expected_lock = canonical_lock_path().resolve()
    if args.lock.resolve() != expected_lock:
        raise ValueError(f"only the canonical benchmark lock is allowed: {expected_lock}")
    if args.output.exists():
        raise FileExistsError(
            f"output already exists; resume is explicitly unsupported: {args.output}"
        )
    check_resources(args.output.parent)
    args.output.mkdir(parents=False)
    rows_path = args.output / "assignment-expansion-rows.jsonl"
    events_path = args.output / "branch-events.jsonl"
    checkpoint_path = args.output / "checkpoint.json"
    manifest_path = args.output / "manifest.json"
    rows_path.touch()
    events_path.touch()
    state: dict[str, Any] = {"count": 0, "digest": ""}
    completed: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "schema": "assignment-expansion-diagnostic-v1",
        "status": "started",
        "mode_count": MODE_COUNT,
        "completed_mode_count": 0,
        "pending_mode_count": MODE_COUNT,
        "local_radius_m": 1000.0,
        "max_nfev": 100,
        "max_branches": 5000,
        "whole_timeout_s": args.timeout_s,
        "reserve_bytes": FLOOR_BYTES,
        "output_cap_bytes": MAX_OUTPUT_BYTES,
        "resume_supported": False,
        "resume_policy": "explicit_noresume",
        "full_domain_complete": False,
        "decision_status": "ambiguous",
        "search_status": "budget_exhausted",
        "certificate_scope": "none",
        "truth_used": False,
        "evaluator_used": False,
        "cpu_only": True,
        "numeric_threads": 1,
        "rows_file": rows_path.name,
        "events_file": events_path.name,
        "checkpoint_file": checkpoint_path.name,
        "source_run_complete": False,
    }
    _write_json(manifest_path, manifest)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_s)
    try:
        with args.lock.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("canonical benchmark lock is already held") from exc
            rows, assigned_manifest = load_assigned_bundle(args.assigned, args.a20)
            a20_rows, a20_manifest = load_a20_bundle(args.a20)
            source_pins = assigned_manifest["source_pins"]
            attempt = Path(source_pins["attempt"])
            static = Path(source_pins["input_paths"]["measurements"]).parent
            config = Path(source_pins["config_path"])
            pins, seeds = verify_source_pins(a20_manifest, args.a20, attempt, static, config)
            validate_mode_source_refs(a20_rows, seeds)
            scorer = build_scorer(static, config)
            from bearing_localization.search.assignment_expansion import (
                ExpansionConfig,
                expand_assignments,
            )

            expansion_config = ExpansionConfig(
                local_radius_m=1000.0, max_nfev=100, max_branches=5000
            )
            manifest.update(
                {
                    "assigned_root": str(args.assigned.resolve()),
                    "assigned_manifest_sha256": sha256(args.assigned / "manifest.json"),
                    "a20_root": str(args.a20.resolve()),
                    "a20_manifest_sha256": sha256(args.a20 / "manifest.json"),
                    "a20_rows_sha256": a20_manifest["rows_sha256"],
                    "source_pins": pins,
                    "harness_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
                    ).strip(),
                    "package_commit": assigned_manifest["package_commit"],
                    "frozen_scoring_source_sha": pins["frozen_scoring_source_sha"],
                    "input_paths": pins["input_paths"],
                    "config_path": pins["config_path"],
                }
            )
            _write_json(manifest_path, manifest)
            with events_path.open("a") as events_stream:

                def record(mode_index: int, phase: str, event: dict[str, Any]) -> None:
                    check_resources(args.output)
                    _append_event(events_stream, state, mode_index, phase, event)
                    check_resources(args.output)

                for mode_index, row in enumerate(rows):
                    check_resources(args.output)
                    chosen_label, _ = choose_best_start(row)
                    exact_by_label: dict[str, Hypothesis] = {}
                    alternatives = []
                    for label, saved in _start_hypotheses(row):
                        exact = _revalidate(scorer, saved)
                        exact_by_label[label] = exact
                        alternatives.append(
                            {
                                "label": label,
                                "selected": label == chosen_label,
                                "saved_hypothesis": asdict(saved),
                                "canonical_hypothesis": asdict(exact),
                                "canonical_match": True,
                            }
                        )
                    try:
                        result = expand_assignments(
                            scorer,
                            exact_by_label[chosen_label],
                            config=expansion_config,
                            progress_callback=lambda event, i=mode_index: record(
                                i, "branch_started", event
                            ),
                        )
                    except Exception as exc:
                        failure = {
                            "status": "mode_exception",
                            "exception_type": type(exc).__name__,
                            "message": str(exc),
                        }
                        record(mode_index, "failure", failure)
                        _checkpoint(
                            checkpoint_path,
                            "interrupted" if isinstance(exc, TimeoutError) else "failed",
                            completed,
                            rows_path,
                            events_path,
                            state,
                            f"{type(exc).__name__}: {exc}",
                        )
                        raise
                    for event in result.branch_events:
                        record(mode_index, "branch_result", dict(event))
                    mode_row = {
                        "mode_index": mode_index,
                        "input_index": row.get("input_index"),
                        "candidate_id": row.get("candidate_id"),
                        "selected_start_label": chosen_label,
                        "start_alternatives": alternatives,
                        "seed": asdict(result.seed),
                        "best_hypothesis": asdict(result.best_hypothesis),
                        "status": result.status,
                        "completed": result.completed,
                        "budget_exhausted": result.budget_exhausted,
                        "counters": result.counters,
                        "branch_event_count": len(result.branch_events),
                        "elapsed_s": result.elapsed_s,
                    }
                    with rows_path.open("a") as stream:
                        stream.write(
                            json.dumps(
                                mode_row, sort_keys=True, allow_nan=False, separators=(",", ":")
                            )
                            + "\n"
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                    completed.append(mode_row)
                    _checkpoint(
                        checkpoint_path, "in_progress", completed, rows_path, events_path, state
                    )
                    manifest["completed_mode_count"] = len(completed)
                    manifest["pending_mode_count"] = MODE_COUNT - len(completed)
                    manifest["elapsed_s"] = time.perf_counter() - started
                    manifest["event_count"] = state["count"]
                    manifest["event_digest"] = state["digest"]
                    _write_json(manifest_path, manifest)
                    check_resources(args.output)
            manifest["status"] = "completed"
            manifest["ranked_selected_modes"] = [
                {
                    "mode_index": item["mode_index"],
                    "candidate_id": item["candidate_id"],
                    "selected_start_label": item["selected_start_label"],
                    "score": item["best_hypothesis"]["score"],
                    "search_status": item["status"],
                }
                for item in sorted(
                    completed,
                    key=lambda item: (item["best_hypothesis"]["score"], item["mode_index"]),
                )
            ]
            _checkpoint(checkpoint_path, "completed", completed, rows_path, events_path, state)
    except TimeoutError as exc:
        manifest["status"] = "interrupted"
        manifest["error"] = str(exc)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        manifest["completed_mode_count"] = len(completed)
        manifest["pending_mode_count"] = MODE_COUNT - len(completed)
        manifest["elapsed_s"] = time.perf_counter() - started
        manifest["rows_sha256"] = sha256(rows_path)
        manifest["events_sha256"] = sha256(events_path)
        manifest["event_count"] = state["count"]
        manifest["event_digest"] = state["digest"]
        manifest["output_bytes"] = _output_bytes(args.output)
        manifest["free_bytes_at_end"] = shutil.disk_usage(args.output).free
        if manifest["output_bytes"] > MAX_OUTPUT_BYTES:
            manifest["status"] = "failed"
            manifest["error"] = "diagnostic output exceeded 20 MiB"
        _write_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assigned", type=Path, required=True)
    parser.add_argument("--a20", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)
    result = run(args)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in ("status", "completed_mode_count", "pending_mode_count")
            }
        )
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
