"""Run a bounded four-worker CPU assignment-expansion diagnostic."""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

from maritime_localization_shared.contracts.localization import Hypothesis
from tools import run_assignment_expansion_diagnostic as serial

MODE_COUNT = serial.MODE_COUNT
FLOOR_BYTES = serial.FLOOR_BYTES
MAX_OUTPUT_BYTES = serial.MAX_OUTPUT_BYTES
DEFAULT_TIMEOUT_S = serial.DEFAULT_TIMEOUT_S
WORKER_COUNT = 4
OUTPUT_NAME = "parallel-assignment-expansion-diagnostic-v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        for row in sorted(rows, key=lambda item: item["mode_index"]):
            stream.write(
                json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
            )
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return serial.sha256(path)


def _output_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _deadline(_signal: int, _frame: Any) -> None:
    raise TimeoutError("whole parallel diagnostic deadline exceeded")


@dataclass(frozen=True, slots=True)
class FixedScorerFactory:
    """Picklable factory that loads the pinned, fixed diagnostic inputs."""

    static_dir: str
    config_path: str
    source_digest: str

    def __call__(self) -> Any:
        return serial.build_scorer(Path(self.static_dir), Path(self.config_path))


@dataclass(slots=True)
class DurableState:
    event_count: int = 0
    event_digest: str = ""
    mode_event_counts: dict[int, int] | None = None
    outcomes: dict[int, dict[str, Any]] | None = None
    failures: dict[int, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.mode_event_counts = {} if self.mode_event_counts is None else self.mode_event_counts
        self.outcomes = {} if self.outcomes is None else self.outcomes
        self.failures = {} if self.failures is None else self.failures


def append_event(
    stream: Any, state: DurableState, mode_index: int, phase: str, event: dict[str, Any]
) -> None:
    if not 0 <= mode_index < MODE_COUNT:
        raise ValueError("event mode_index is outside the fixed mode inventory")
    assert state.mode_event_counts is not None
    local_index = state.mode_event_counts.get(mode_index, 0)
    payload = {
        "mode_index": mode_index,
        "mode_event_index": local_index,
        "phase": phase,
        "event_index": state.event_count,
        "event": event,
        "previous_event_sha256": state.event_digest,
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    payload["event_sha256"] = digest
    stream.write(json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
    state.event_count += 1
    state.event_digest = digest
    state.mode_event_counts[mode_index] = local_index + 1


def _checkpoint(
    path: Path,
    status: str,
    rows_path: Path,
    events_path: Path,
    state: DurableState,
    error: str | None = None,
) -> None:
    assert (
        state.outcomes is not None
        and state.failures is not None
        and state.mode_event_counts is not None
    )
    completed, failed = sorted(state.outcomes), sorted(state.failures)
    known = set(completed) | set(failed)
    value: dict[str, Any] = {
        "schema": "parallel-assignment-expansion-checkpoint-v1",
        "status": status,
        "resume_supported": False,
        "completed_mode_indices": completed,
        "failed_mode_indices": failed,
        "unknown_mode_indices": [index for index in range(MODE_COUNT) if index not in known],
        "completed_mode_count": len(completed),
        "failed_mode_count": len(failed),
        "unknown_mode_count": MODE_COUNT - len(known),
        "rows_sha256": _sha256(rows_path),
        "events_sha256": _sha256(events_path),
        "event_count": state.event_count,
        "event_digest": state.event_digest,
        "mode_event_counts": {
            str(key): value for key, value in sorted(state.mode_event_counts.items())
        },
    }
    if error is not None:
        value["error"] = error
    _atomic_json(path, value)


def _persist_state(
    output: Path,
    manifest: dict[str, Any],
    state: DurableState,
    status: str = "in_progress",
    error: str | None = None,
) -> None:
    assert (
        state.outcomes is not None
        and state.failures is not None
        and state.mode_event_counts is not None
    )
    rows_path, events_path = (
        output / "assignment-expansion-rows.jsonl",
        output / "branch-events.jsonl",
    )
    _write_rows(rows_path, list(state.outcomes.values()))
    completed, failed = sorted(state.outcomes), sorted(state.failures)
    known = set(completed) | set(failed)
    manifest.update(
        {
            "completed_mode_count": len(completed),
            "failed_mode_count": len(failed),
            "unknown_mode_count": MODE_COUNT - len(known),
            "completed_mode_indices": completed,
            "failed_mode_indices": failed,
            "unknown_mode_indices": [index for index in range(MODE_COUNT) if index not in known],
            "event_count": state.event_count,
            "event_digest": state.event_digest,
            "mode_event_counts": {
                str(key): value for key, value in sorted(state.mode_event_counts.items())
            },
            "failures": {str(key): value for key, value in sorted(state.failures.items())},
        }
    )
    _checkpoint(output / "checkpoint.json", status, rows_path, events_path, state, error)
    _atomic_json(output / "manifest.json", manifest)


def _input_digest(
    mode_index: int,
    row: dict[str, Any],
    label: str,
    seed: Hypothesis,
    pins: dict[str, Any],
    candidate_branch_count: int,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "mode_index": mode_index,
                "candidate_id": row.get("candidate_id"),
                "selected_start_label": label,
                "seed": serial.asdict(seed),
                "source_pins": pins,
                "local_radius_m": 1000.0,
                "max_nfev": 100,
                "max_branches": 5000,
                "candidate_branch_count": candidate_branch_count,
            }
        )
    ).hexdigest()


def prepare_mode_tasks(
    rows: list[dict[str, Any]],
    pins: dict[str, Any],
    identity: dict[str, str],
    expansion_config: Any,
    scorer: Any,
) -> tuple[list[Any], dict[int, dict[str, Any]]]:
    """Choose and revalidate the same two-start inputs as the serial runner."""
    from bearing_localization.search.parallel_assignment_expansion import ModeTask
    from bearing_localization.search.assignment_expansion import _candidate_pool

    tasks, selected_starts = [], {}
    for mode_index, row in enumerate(rows):
        label, _saved_seed = serial.choose_best_start(row)
        exact_by_label, alternatives = {}, []
        for start_label, source_hypothesis in serial._start_hypotheses(row):
            exact = serial._revalidate(scorer, source_hypothesis)
            exact_by_label[start_label] = exact
            alternatives.append(
                {
                    "label": start_label,
                    "selected": start_label == label,
                    "saved_hypothesis": serial.asdict(source_hypothesis),
                    "canonical_hypothesis": serial.asdict(exact),
                    "canonical_match": True,
                }
            )
        selected = exact_by_label[label]
        _eligible, pool_counts, _pool_digest = _candidate_pool(scorer, selected, expansion_config)
        candidate_branch_count = int(pool_counts["eligible_records"]) * int(
            pool_counts["unmatched_reports"]
        )
        selected_starts[mode_index] = {
            "input_index": row.get("input_index"),
            "candidate_id": row.get("candidate_id"),
            "selected_start_label": label,
            "start_alternatives": alternatives,
            "candidate_branch_count": candidate_branch_count,
        }
        tasks.append(
            ModeTask(
                mode_index=mode_index,
                seed=selected,
                config=expansion_config,
                package_commit=identity["package_commit"],
                source_digest=hashlib.sha256(_canonical(pins)).hexdigest(),
                input_digest=_input_digest(
                    mode_index, row, label, selected, pins, candidate_branch_count
                ),
                candidate_branch_count=candidate_branch_count,
            )
        )
    return tasks, selected_starts


def _loaded_package_identity() -> dict[str, str]:
    provenance = serial.package_provenance()
    repo_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
    ).strip()
    package_path = Path(provenance["package_source"]).resolve()
    module_path = Path(provenance["assignment_expansion_source"]).resolve()
    try:
        package_path.relative_to(_REPO_ROOT)
        module_path.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise serial.DiagnosticInputError(
            "loaded package resolves outside the pinned repository checkout"
        ) from exc
    if provenance["package_commit"] != repo_commit:
        raise serial.DiagnosticInputError(
            "loaded package commit differs from current harness commit"
        )
    return {
        "package_commit": repo_commit,
        "package_source": str(package_path),
        "assignment_expansion_source": str(module_path),
        "assignment_expansion_sha256": _sha256(module_path),
    }


def _validate_worker_provenance(
    values: list[dict[str, Any]], expected: dict[str, str], *, require_all: bool = True
) -> None:
    if require_all and len(values) != WORKER_COUNT:
        raise serial.DiagnosticInputError("pool did not report provenance for all four workers")
    worker_ids = [item.get("worker_id") for item in values]
    if len(set(worker_ids)) != WORKER_COUNT:
        raise serial.DiagnosticInputError("worker provenance IDs are not unique")
    for item in values:
        if item.get("package_commit") != expected["package_commit"]:
            raise serial.DiagnosticInputError("worker loaded a package from a different commit")
        if item.get("assignment_expansion_sha256") != expected["assignment_expansion_sha256"]:
            raise serial.DiagnosticInputError(
                "worker loaded assignment-expansion source differs from coordinator"
            )
        if Path(str(item.get("assignment_expansion_source"))).resolve() != Path(
            expected["assignment_expansion_source"]
        ):
            raise serial.DiagnosticInputError(
                "worker loaded assignment-expansion source from an unexpected path"
            )
        scorer_source = Path(str(item.get("scorer_source", ""))).resolve()
        if not item.get("scorer_sha256") or not item.get("scorer_source"):
            raise serial.DiagnosticInputError("worker omitted scorer source provenance")
        try:
            scorer_source.relative_to(_REPO_ROOT)
        except ValueError as exc:
            raise serial.DiagnosticInputError(
                "worker scorer source resolves outside the pinned checkout"
            ) from exc
        if _sha256(scorer_source) != item["scorer_sha256"]:
            raise serial.DiagnosticInputError(
                "worker scorer source hash does not match the checkout"
            )


def _callback_event(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        return converter()
    raise TypeError("pool branch callback must be a mapping or expose as_dict()")


def _branch_inventory(rows: list[dict[str, Any]]) -> dict[str, int]:
    counters = [row["counters"] for row in rows]
    return {
        "candidate_branches": sum(int(item.get("candidate_branches", 0)) for item in counters),
        "visited_branches": sum(int(item.get("visited_branches", 0)) for item in counters),
        "unvisited_branches": sum(int(item.get("unvisited_branches", 0)) for item in counters),
    }


def _dispatch_order(tasks: list[Any]) -> list[Any]:
    return sorted(tasks, key=lambda task: (-task.candidate_branch_count, task.mode_index))


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if not math.isfinite(args.timeout_s) or not 0.0 < args.timeout_s <= DEFAULT_TIMEOUT_S:
        raise ValueError("timeout must be finite and in (0, 1800]")
    if getattr(args, "worker_count", WORKER_COUNT) != WORKER_COUNT:
        raise ValueError("parallel diagnostic requires exactly four workers")
    expected_lock = serial.canonical_lock_path().resolve()
    if args.lock.resolve() != expected_lock:
        raise ValueError(f"only the canonical benchmark lock is allowed: {expected_lock}")
    if args.output.name != OUTPUT_NAME:
        raise ValueError(f"output directory must be the unique {OUTPUT_NAME} path")
    if args.output.exists():
        raise FileExistsError(
            f"output already exists; resume is explicitly unsupported: {args.output}"
        )
    serial.check_resources(args.output.parent, args.output)
    args.output.mkdir(parents=False)
    rows_path, events_path = (
        args.output / "assignment-expansion-rows.jsonl",
        args.output / "branch-events.jsonl",
    )
    checkpoint_path, manifest_path = args.output / "checkpoint.json", args.output / "manifest.json"
    rows_path.touch()
    events_path.touch()
    state = DurableState()
    manifest: dict[str, Any] = {
        "schema": "parallel-assignment-expansion-diagnostic-v1",
        "status": "started",
        "mode_count": MODE_COUNT,
        "worker_count": WORKER_COUNT,
        "completed_mode_count": 0,
        "failed_mode_count": 0,
        "unknown_mode_count": MODE_COUNT,
        "completed_mode_indices": [],
        "failed_mode_indices": [],
        "unknown_mode_indices": list(range(MODE_COUNT)),
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
    _atomic_json(manifest_path, manifest)
    signal.signal(signal.SIGALRM, _deadline)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_s)
    try:
        with args.lock.open("a") as lock_stream:
            try:
                fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("canonical benchmark lock is already held") from exc
            validation_started = time.perf_counter()
            assigned_rows, assigned_manifest = serial.load_assigned_bundle(args.assigned, args.a20)
            a20_rows, a20_manifest = serial.load_a20_bundle(args.a20)
            source_pins = assigned_manifest["source_pins"]
            attempt = Path(source_pins["attempt"])
            static = Path(source_pins["input_paths"]["measurements"]).parent
            config_path = Path(source_pins["config_path"])
            pins, seeds = serial.verify_source_pins(
                a20_manifest, args.a20, attempt, static, config_path
            )
            serial.validate_mode_source_refs(a20_rows, seeds)
            identity = _loaded_package_identity()
            scorer = serial.build_scorer(static, config_path)
            from bearing_localization.search.assignment_expansion import ExpansionConfig
            from bearing_localization.search.parallel_assignment_expansion import (
                ParallelExpansionError,
                run_expansion_modes,
            )

            expansion_config = ExpansionConfig(
                local_radius_m=1000.0, max_nfev=100, max_branches=5000
            )
            tasks, selected_starts = prepare_mode_tasks(
                assigned_rows, pins, identity, expansion_config, scorer
            )
            tasks = _dispatch_order(tasks)
            validation_elapsed = time.perf_counter() - validation_started
            del scorer
            factory = FixedScorerFactory(
                str(static.resolve()),
                str(config_path.resolve()),
                hashlib.sha256(_canonical(pins)).hexdigest(),
            )
            manifest.update(
                {
                    "assigned_root": str(args.assigned.resolve()),
                    "assigned_manifest_sha256": _sha256(args.assigned / "manifest.json"),
                    "a20_root": str(args.a20.resolve()),
                    "a20_manifest_sha256": _sha256(args.a20 / "manifest.json"),
                    "a20_rows_sha256": a20_manifest["rows_sha256"],
                    "source_pins": pins,
                    "harness_commit": identity["package_commit"],
                    "input_assigned_package_commit": assigned_manifest["package_commit"],
                    "frozen_scoring_source_sha": pins["frozen_scoring_source_sha"],
                    "input_paths": pins["input_paths"],
                    "config_path": pins["config_path"],
                    "coordinator_loaded_package": identity,
                    "runner_sha256": _sha256(Path(__file__).resolve()),
                    "validation_and_setup_elapsed_s": validation_elapsed,
                    "dispatch_order": [task.mode_index for task in tasks],
                    "preflight_branch_counts": {
                        str(task.mode_index): task.candidate_branch_count for task in tasks
                    },
                    "dispatch_count_source": (
                        "eligible records times unmatched reports from _candidate_pool"
                    ),
                }
            )
            _atomic_json(manifest_path, manifest)
            with events_path.open("a") as event_stream:

                def branch_event(value: Any) -> None:
                    event = _callback_event(value)
                    mode_index = event.get("mode_index")
                    if type(mode_index) is not int:
                        raise RuntimeError("worker callback omitted mode_index")
                    if time.perf_counter() - started >= args.timeout_s:
                        raise TimeoutError("whole parallel diagnostic deadline exceeded")
                    serial.check_resources(args.output.parent, args.output)
                    phase = str(event.get("phase", "branch_event"))
                    append_event(event_stream, state, mode_index, phase, event)
                    serial.check_resources(args.output.parent, args.output)

                def mode_outcome(outcome: Any) -> None:
                    mode_index = getattr(outcome, "mode_index", None)
                    expansion = getattr(outcome, "result", None)
                    if type(mode_index) is not int or not 0 <= mode_index < MODE_COUNT:
                        raise RuntimeError("worker outcome omitted a valid mode_index")
                    assert state.outcomes is not None and state.failures is not None
                    if mode_index in state.outcomes or mode_index in state.failures:
                        raise RuntimeError(f"duplicate mode outcome {mode_index}")
                    if expansion is None:
                        failure = {
                            "status": "mode_failed",
                            "error": str(getattr(outcome, "error", "unknown worker failure")),
                        }
                        state.failures[mode_index] = failure
                        append_event(event_stream, state, mode_index, "mode_failure", failure)
                    else:
                        expected_count = int(expansion.counters.get("visited_branches", 0))
                        if len(expansion.branch_events) != expected_count:
                            raise RuntimeError(f"mode {mode_index} branch inventory is incomplete")
                        state.outcomes[mode_index] = {
                            "mode_index": mode_index,
                            **selected_starts[mode_index],
                            "seed": serial.asdict(expansion.seed),
                            "best_hypothesis": serial.asdict(expansion.best_hypothesis),
                            "status": expansion.status,
                            "completed": expansion.completed,
                            "budget_exhausted": expansion.budget_exhausted,
                            "counters": expansion.counters,
                            "branch_event_count": len(expansion.branch_events),
                            "elapsed_s": expansion.elapsed_s,
                        }
                        append_event(
                            event_stream,
                            state,
                            mode_index,
                            "mode_result",
                            {
                                "status": expansion.status,
                                "branch_event_count": len(expansion.branch_events),
                                "counters": expansion.counters,
                            },
                        )
                    _persist_state(args.output, manifest, state)
                    serial.check_resources(args.output.parent, args.output)

                remaining = max(0.001, args.timeout_s - (time.perf_counter() - started))
                pool_failure = None
                try:
                    pool_result = run_expansion_modes(
                        tasks,
                        factory,
                        worker_count=WORKER_COUNT,
                        progress_callback=branch_event,
                        result_callback=mode_outcome,
                        deadline_s=remaining,
                    )
                except ParallelExpansionError as exc:
                    pool_failure = exc
                    pool_result = exc.partial_result
            worker_records = [
                dict(item) if isinstance(item, dict) else asdict(item)
                for item in getattr(pool_result, "worker_provenance", ())
                if isinstance(item, dict) or is_dataclass(item)
            ]
            _validate_worker_provenance(worker_records, identity, require_all=pool_failure is None)
            manifest["worker_provenance"] = worker_records
            for key in (
                "completed_mode_ids",
                "failed_mode_ids",
                "pending_mode_ids",
                "not_dispatched_mode_ids",
            ):
                manifest[key] = sorted(getattr(pool_result, key, ()))
            worker_rss = [
                item.get("peak_rss_bytes", item.get("rss_bytes")) for item in worker_records
            ]
            manifest["worker_memory"] = {
                "worker_rss_at_ready_bytes": [
                    item.get("rss_bytes_at_ready") for item in worker_records
                ],
                "worker_rss_bytes": worker_rss,
                "peak_worker_rss_bytes": max(
                    (value for value in worker_rss if isinstance(value, int)), default=None
                ),
                "measurement_available": all(isinstance(value, int) for value in worker_rss),
            }
            assert state.outcomes is not None and state.failures is not None
            missing = set(range(MODE_COUNT)) - set(state.outcomes) - set(state.failures)
            failed_from_pool = sorted(
                set(getattr(pool_result, "failed_mode_ids", ())) - state.failures.keys()
            )
            with events_path.open("a") as event_stream:
                for mode_index in failed_from_pool:
                    failure = {"status": "worker_failed"}
                    state.failures[mode_index] = failure
                    append_event(event_stream, state, mode_index, "mode_failure", failure)
            missing -= set(state.failures)
            if missing:
                manifest["unknown_mode_indices"] = sorted(missing)
            if pool_failure is not None:
                error = str(pool_failure)
                status = "interrupted" if "deadline exceeded" in error else "failed"
                _persist_state(args.output, manifest, state, status, error)
                if status == "interrupted":
                    raise TimeoutError(error)
                raise RuntimeError(error)
            if (
                state.failures
                or len(state.outcomes) != MODE_COUNT
                or not getattr(pool_result, "complete", False)
            ):
                _persist_state(
                    args.output,
                    manifest,
                    state,
                    "failed",
                    "parallel worker pool did not complete all modes",
                )
                raise RuntimeError("parallel worker pool did not complete all modes")
            branch_inventory = _branch_inventory(list(state.outcomes.values()))
            expected_branches = sum(task.candidate_branch_count for task in tasks)
            manifest["branch_inventory"] = {
                **branch_inventory,
                "preflight_candidate_branches": expected_branches,
                "coverage_complete": (
                    branch_inventory["candidate_branches"] == expected_branches
                    and branch_inventory["visited_branches"] == expected_branches
                    and branch_inventory["unvisited_branches"] == 0
                ),
            }
            if not manifest["branch_inventory"]["coverage_complete"]:
                _persist_state(
                    args.output,
                    manifest,
                    state,
                    "failed",
                    "branch inventory is incomplete or differs from preflight",
                )
                raise RuntimeError("branch inventory is incomplete or differs from preflight")
            manifest["ranked_selected_modes"] = [
                {
                    "mode_index": item["mode_index"],
                    "candidate_id": item["candidate_id"],
                    "selected_start_label": item["selected_start_label"],
                    "score": item["best_hypothesis"]["score"],
                    "search_status": item["status"],
                }
                for item in sorted(
                    state.outcomes.values(),
                    key=lambda item: (item["best_hypothesis"]["score"], item["mode_index"]),
                )
            ]
            manifest.update({"status": "completed", "source_run_complete": True})
            _persist_state(args.output, manifest, state, "completed")
    except TimeoutError as exc:
        manifest["status"], manifest["error"] = "interrupted", str(exc)
        try:
            _persist_state(args.output, manifest, state, "interrupted", str(exc))
        except OSError:
            pass
    except Exception as exc:
        manifest["status"], manifest["error"] = "failed", f"{type(exc).__name__}: {exc}"
        try:
            _persist_state(args.output, manifest, state, "failed", manifest["error"])
        except OSError:
            pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        import shutil

        manifest.update(
            {
                "completed_mode_count": len(state.outcomes or {}),
                "failed_mode_count": len(state.failures or {}),
                "unknown_mode_count": MODE_COUNT
                - len(state.outcomes or {})
                - len(state.failures or {}),
                "elapsed_s": time.perf_counter() - started,
                "rows_sha256": _sha256(rows_path),
                "events_sha256": _sha256(events_path),
                "output_bytes": _output_bytes(args.output),
                "free_bytes_at_end": shutil.disk_usage(args.output).free,
            }
        )
        if manifest["output_bytes"] > MAX_OUTPUT_BYTES:
            manifest["status"], manifest["error"] = (
                "failed",
                f"diagnostic output exceeded {MAX_OUTPUT_BYTES // 1024**2} MiB",
            )
            _checkpoint(
                checkpoint_path,
                "failed",
                rows_path,
                events_path,
                state,
                manifest["error"],
            )
        if not checkpoint_path.exists():
            _persist_state(args.output, manifest, state, manifest["status"], manifest.get("error"))
        _atomic_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assigned", type=Path, required=True)
    parser.add_argument("--a20", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".agent-work/outputs") / OUTPUT_NAME)
    parser.add_argument("--lock", type=Path, default=serial.canonical_lock_path())
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)
    result = run(args)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "status",
                    "completed_mode_count",
                    "failed_mode_count",
                    "unknown_mode_count",
                )
            }
        )
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
