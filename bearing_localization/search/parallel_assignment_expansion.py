"""Bounded spawned CPU execution for independent assignment-expansion modes."""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import queue
import resource
import subprocess
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Any, Protocol

from maritime_localization_shared.contracts.localization import Hypothesis

from .assignment_expansion import ExpansionConfig, ExpansionResult, expand_assignments
from .scoring import CandidateScorer


class ScorerFactory(Protocol):
    """Picklable top-level callable that loads one worker-local scorer."""

    @property
    def source_digest(self) -> str: ...

    def __call__(self) -> CandidateScorer: ...


@dataclass(frozen=True, slots=True)
class ModeTask:
    """One independent whole-mode expansion with shared input provenance."""

    mode_index: int
    seed: Hypothesis
    config: ExpansionConfig
    candidate_branch_count: int
    package_commit: str
    input_digest: str
    source_digest: str

    def __post_init__(self) -> None:
        if type(self.mode_index) is not int or self.mode_index < 0:
            raise ValueError("mode_index must be a nonnegative integer.")
        if not isinstance(self.seed, Hypothesis):
            raise TypeError("seed must be a Hypothesis.")
        if not isinstance(self.config, ExpansionConfig):
            raise TypeError("config must be an ExpansionConfig.")
        if type(self.candidate_branch_count) is not int or self.candidate_branch_count < 0:
            raise ValueError("candidate_branch_count must be a nonnegative integer.")
        if not self.package_commit or not self.input_digest or not self.source_digest:
            raise ValueError("package_commit, input_digest, and source_digest must be nonempty.")


@dataclass(frozen=True, slots=True)
class ModeOutcome:
    mode_index: int
    result: ExpansionResult


@dataclass(frozen=True, slots=True)
class WorkerProvenance:
    worker_id: int
    pid: int
    package_commit: str
    assignment_expansion_source: str
    assignment_expansion_sha256: str
    scorer_source: str
    scorer_sha256: str
    rss_bytes_at_ready: int
    peak_rss_bytes: int


@dataclass(frozen=True, slots=True)
class ExpansionRunResult:
    outcomes: tuple[ModeOutcome, ...]
    completed_mode_ids: tuple[int, ...]
    failed_mode_ids: tuple[int, ...]
    pending_mode_ids: tuple[int, ...]
    not_dispatched_mode_ids: tuple[int, ...]
    worker_provenance: tuple[WorkerProvenance, ...]
    summary: dict[str, int]
    elapsed_s: float
    complete: bool


class ParallelExpansionError(RuntimeError):
    """Pool failure with an exact mode-level disposition snapshot."""

    def __init__(
        self, message: str, *, partial_result: ExpansionRunResult, cause_text: str | None = None
    ) -> None:
        super().__init__(message)
        self.partial_result = partial_result
        self.cause_text = cause_text


def _rss_bytes() -> int:
    # Linux reports ru_maxrss in KiB.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)


def _sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _provenance(worker_id: int) -> WorkerProvenance:
    from . import assignment_expansion, scoring

    expansion_path = str(Path(assignment_expansion.__file__).resolve())
    scoring_path = str(Path(scoring.__file__).resolve())
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(expansion_path).parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not determine loaded package commit") from exc
    rss = _rss_bytes()
    return WorkerProvenance(
        worker_id,
        os.getpid(),
        commit,
        expansion_path,
        _sha(expansion_path),
        scoring_path,
        _sha(scoring_path),
        rss,
        rss,
    )


def _worker(worker_id: int, task_q: Any, output_q: Any, factory: ScorerFactory) -> None:
    try:
        scorer = factory()
        if not isinstance(scorer, CandidateScorer):
            raise TypeError("scorer_factory must return CandidateScorer.")
        provenance = _provenance(worker_id)
        output_q.put(("ready", worker_id, provenance))
    except BaseException as exc:
        output_q.put(
            ("init_error", worker_id, f"{type(exc).__name__}: {exc}", traceback.format_exc())
        )
        return
    peak = provenance.peak_rss_bytes
    while True:
        task = task_q.get()
        if task is None:
            return
        output_q.put(("started", worker_id, task.mode_index))
        try:

            def emit(phase: str, event: dict[str, Any]) -> None:
                output_q.put(("branch", task.mode_index, phase, event))

            result = expand_assignments(
                scorer,
                task.seed,
                config=task.config,
                progress_callback=lambda event: emit("branch_started", event),
                result_callback=lambda event: emit("branch_result", event),
            )
            peak = max(peak, _rss_bytes())
            output_q.put(("rss", worker_id, peak))
            output_q.put(("done", worker_id, ModeOutcome(task.mode_index, result)))
        except BaseException as exc:
            output_q.put(
                (
                    "mode_error",
                    worker_id,
                    task.mode_index,
                    f"{type(exc).__name__}: {exc}",
                    traceback.format_exc(),
                )
            )


def _reap(processes: Sequence[Any], grace_s: float = 0.5) -> None:
    for proc in processes:
        if proc.pid is None:
            continue
        if proc.is_alive():
            proc.join(timeout=grace_s)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=grace_s)
            if proc.is_alive() and hasattr(proc, "kill"):
                proc.kill()
                proc.join(timeout=grace_s)


def _summary(outcomes: Sequence[ModeOutcome]) -> dict[str, int]:
    keys = ("visited_branches", "unvisited_branches", "canonical_evaluations", "improved_branches")
    result = {"mode_count": len(outcomes), "branch_events": 0, **{key: 0 for key in keys}}
    for item in outcomes:
        result["branch_events"] += len(item.result.branch_events)
        for key in keys:
            value = item.result.counters.get(key, 0)
            if isinstance(value, int):
                result[key] += value
    return result


def run_expansion_modes(
    tasks: Sequence[ModeTask],
    scorer_factory: ScorerFactory,
    *,
    worker_count: int = 1,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    result_callback: Callable[[ModeOutcome], None] | None = None,
    deadline_s: float | None = None,
) -> ExpansionRunResult:
    """Run whole modes in spawned workers with bounded dispatch.

    deadline_s is an elapsed timeout covering worker startup and all jobs.
    At most two times worker_count modes are in flight. Each worker emits a
    mode's branch events in original branch order; modes may interleave.
    """
    started_at = time.monotonic()
    if worker_count not in (1, 4):
        raise ValueError("worker_count must be one or four.")
    if deadline_s is not None and (deadline_s <= 0 or not isfinite(deadline_s)):
        raise ValueError("deadline_s must be finite and positive.")
    modes = tuple(tasks)
    ids = tuple(task.mode_index for task in modes)
    if len(set(ids)) != len(ids):
        raise ValueError("mode_index values must be unique.")
    if modes and len({(task.package_commit, task.source_digest) for task in modes}) != 1:
        raise ValueError("all modes must use one package commit and static source digest.")
    if not modes:
        return ExpansionRunResult((), (), (), (), (), (), _summary(()), 0.0, True)

    ctx = mp.get_context("spawn")
    window = min(len(modes), 2 * worker_count)
    task_q = ctx.Queue(maxsize=window)
    output_q = ctx.Queue(maxsize=8 * worker_count)
    env_keys = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
    old_env = {key: os.environ.get(key) for key in env_keys}
    os.environ.update(dict.fromkeys(env_keys, "1"))
    processes = [
        ctx.Process(target=_worker, args=(i, task_q, output_q, scorer_factory))
        for i in range(worker_count)
    ]
    started_processes: list[Any] = []
    try:
        for proc in processes:
            proc.start()
            started_processes.append(proc)
    except BaseException as exc:
        _reap(started_processes)
        for channel in (task_q, output_q):
            try:
                channel.close()
                channel.join_thread()
            except (OSError, ValueError):
                pass
        partial = ExpansionRunResult(
            (),
            (),
            (),
            (),
            tuple(sorted(ids)),
            (),
            _summary(()),
            time.monotonic() - started_at,
            False,
        )
        raise ParallelExpansionError(
            "worker process startup failed",
            partial_result=partial,
            cause_text=traceback.format_exc(),
        ) from exc
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    provenance: dict[int, WorkerProvenance] = {}
    outcomes: dict[int, ModeOutcome] = {}
    failed: set[int] = set()
    sent: set[int] = set()
    current: dict[int, int] = {}
    next_index = 0
    reason: str | None = None
    cause: str | None = None
    deadline = None if deadline_s is None else started_at + deadline_s

    def result_snapshot() -> ExpansionRunResult:
        completed = tuple(sorted(outcomes))
        failed_ids = tuple(sorted(failed - set(outcomes)))
        pending = tuple(sorted(sent - set(outcomes) - failed))
        not_sent = tuple(sorted(set(ids) - sent))
        output = tuple(outcomes[index] for index in completed)
        return ExpansionRunResult(
            output,
            completed,
            failed_ids,
            pending,
            not_sent,
            tuple(provenance[index] for index in sorted(provenance)),
            _summary(output),
            time.monotonic() - started_at,
            not (failed_ids or pending or not_sent),
        )

    try:
        while len(provenance) < worker_count:
            if deadline is not None and time.monotonic() >= deadline:
                reason = "deadline exceeded during worker initialization"
                break
            try:
                message = output_q.get(timeout=0.05)
            except queue.Empty:
                dead = [proc for proc in processes if proc.exitcode is not None]
                if dead:
                    reason = "worker exited before initialization completed"
                    cause = ", ".join(f"pid={p.pid} exitcode={p.exitcode}" for p in dead)
                    break
                continue
            if message[0] == "ready":
                _, worker_id, worker_info = message
                provenance[worker_id] = worker_info
            elif message[0] == "init_error":
                _, worker_id, err, trace = message
                reason, cause = f"worker {worker_id} initialization failed: {err}", trace
                break

        if reason is None:
            expected_commit = modes[0].package_commit
            expected_source_digest = modes[0].source_digest
            if any(item.package_commit != expected_commit for item in provenance.values()):
                reason = "worker loaded package commit differs from ModeTask.package_commit"
            elif getattr(scorer_factory, "source_digest", None) != expected_source_digest:
                reason = "scorer_factory.source_digest differs from ModeTask.source_digest"

        while reason is None and len(outcomes) + len(failed) < len(modes):
            while next_index < len(modes) and len(sent - set(outcomes) - failed) < window:
                task = modes[next_index]
                sent.add(task.mode_index)
                try:
                    task_q.put(task, timeout=0.5)
                except BaseException as exc:
                    sent.remove(task.mode_index)
                    reason, cause = f"could not submit mode {task.mode_index}", repr(exc)
                    break
                next_index += 1
            if reason is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                reason = "deadline exceeded while expanding modes"
                break
            try:
                message = output_q.get(timeout=0.05)
            except queue.Empty:
                dead = {i for i, proc in enumerate(processes) if proc.exitcode is not None}
                if dead:
                    failed.update(mode for worker, mode in current.items() if worker in dead)
                    reason = "worker exited unexpectedly during mode expansion"
                    cause = ", ".join(
                        f"pid={p.pid} exitcode={p.exitcode}"
                        for p in processes
                        if p.exitcode is not None
                    )
                    break
                continue
            kind = message[0]
            if kind == "started":
                _, worker_id, mode_id = message
                current[worker_id] = mode_id
            elif kind == "branch":
                _, mode_id, phase, event = message
                if progress_callback is not None:
                    try:
                        tagged = dict(event)
                        tagged["mode_index"] = mode_id
                        tagged["phase"] = phase
                        progress_callback(tagged)
                    except BaseException as exc:
                        reason = f"progress callback failed: {type(exc).__name__}: {exc}"
                        cause = traceback.format_exc()
                        break
            elif kind == "rss":
                _, worker_id, rss = message
                if worker_id in provenance:
                    p = provenance[worker_id]
                    provenance[worker_id] = replace(p, peak_rss_bytes=max(p.peak_rss_bytes, rss))
            elif kind == "done":
                _, worker_id, outcome = message
                if outcome.mode_index not in sent:
                    reason = f"worker returned unsubmitted mode {outcome.mode_index}"
                    break
                outcomes[outcome.mode_index] = outcome
                current.pop(worker_id, None)
                if result_callback is not None:
                    try:
                        result_callback(outcome)
                    except BaseException as exc:
                        reason = f"result callback failed: {type(exc).__name__}: {exc}"
                        cause = traceback.format_exc()
                        break
            elif kind == "mode_error":
                _, worker_id, mode_id, err, trace = message
                failed.add(mode_id)
                current.pop(worker_id, None)
                reason, cause = f"mode {mode_id} failed: {err}", trace
            elif kind == "init_error":
                _, worker_id, err, trace = message
                reason, cause = f"worker {worker_id} initialization failed: {err}", trace

        if reason is None:
            for _ in processes:
                task_q.put(None, timeout=0.5)
            for proc in processes:
                proc.join(timeout=1.0)
                if proc.is_alive():
                    reason = "worker did not exit after normal shutdown"
                    break
                if proc.exitcode != 0:
                    reason = f"worker exited with code {proc.exitcode}"
                    break
    except BaseException as exc:
        reason, cause = f"coordinator failed: {type(exc).__name__}: {exc}", traceback.format_exc()
    finally:
        if reason is not None:
            _reap(processes)
        for channel in (task_q, output_q):
            try:
                channel.close()
                channel.join_thread()
            except (OSError, ValueError):
                pass

    final = result_snapshot()
    if reason is not None:
        raise ParallelExpansionError(reason, partial_result=final, cause_text=cause)
    return final


__all__ = [
    "ExpansionRunResult",
    "ModeOutcome",
    "ModeTask",
    "ParallelExpansionError",
    "ScorerFactory",
    "WorkerProvenance",
    "run_expansion_modes",
]
