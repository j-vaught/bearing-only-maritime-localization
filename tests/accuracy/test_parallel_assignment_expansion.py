"""Spawned assignment-expansion parity and lifecycle checks."""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from maritime.search import parallel_assignment_expansion as parallel
from maritime.search.assignment_expansion import ExpansionConfig, expand_assignments

from .test_assignment_expansion import _scorer_with_fifth, _single_addition_seed

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIGEST = "static-fixture-digest"


def _commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _scorer_and_seed():
    scorer = _scorer_with_fifth(extra_catalog_records=2)
    seed, _ = _single_addition_seed(scorer)
    return scorer, seed


@dataclass(frozen=True)
class _Factory:
    source_digest: str = STATIC_DIGEST
    fail_init: bool = False
    slow_init_s: float = 0.0
    crash_exit: int | None = None
    fail_mode: bool = False
    delay_by_budget: bool = False
    delay_mode_s: float = 0.0

    def __call__(self):
        if self.fail_init:
            raise RuntimeError("intentional initializer error")
        if self.slow_init_s:
            time.sleep(self.slow_init_s)
        scorer, _ = _scorer_and_seed()
        if self.crash_exit is not None:
            import maritime.search.parallel_assignment_expansion as module

            exit_code = self.crash_exit

            def crash(*_args: Any, **_kwargs: Any) -> None:
                os._exit(exit_code)

            setattr(module, "expand_assignments", crash)
        elif self.fail_mode or self.delay_by_budget or self.delay_mode_s:
            import maritime.search.parallel_assignment_expansion as module

            original = module.expand_assignments

            def wrapped(scorer_arg, seed_arg, *, config, **kwargs):
                if self.delay_by_budget and config.max_branches == 100:
                    time.sleep(0.4)
                if self.delay_mode_s and config.max_branches == 100:
                    time.sleep(self.delay_mode_s)
                if self.fail_mode:
                    raise RuntimeError("intentional mode error")
                return original(scorer_arg, seed_arg, config=config, **kwargs)

            setattr(module, "expand_assignments", wrapped)
        return scorer


def _tasks(count: int = 4, *, max_branches: int = 20):
    _, seed = _scorer_and_seed()
    commit = _commit()
    return tuple(
        parallel.ModeTask(
            mode_index=index,
            seed=seed,
            config=ExpansionConfig(max_branches=max_branches),
            candidate_branch_count=2,
            package_commit=commit,
            input_digest=f"mode-input-{index}",
            source_digest=STATIC_DIGEST,
        )
        for index in range(count)
    )


def _assert_pids_reaped(result: parallel.ExpansionRunResult) -> None:
    for worker in result.worker_provenance:
        with pytest.raises(ProcessLookupError):
            os.kill(worker.pid, 0)


def _assert_complete_partition(error: parallel.ParallelExpansionError, all_ids: set[int]):
    result = error.partial_result
    categories = (
        set(result.completed_mode_ids),
        set(result.failed_mode_ids),
        set(result.pending_mode_ids),
        set(result.not_dispatched_mode_ids),
    )
    assert all(
        not (left & right) for i, left in enumerate(categories) for right in categories[i + 1 :]
    )
    assert set.union(*categories) == all_ids
    assert not result.complete


def _equivalent(left, right):
    assert left.status == right.status
    assert left.completed == right.completed
    assert left.budget_exhausted == right.budget_exhausted
    assert left.best_hypothesis == right.best_hypothesis
    assert left.counters == right.counters
    assert left.branch_events == right.branch_events


def test_one_and_four_workers_match_serial_and_stream_ordered_branch_events():
    tasks = _tasks()
    serial_scorer, _ = _scorer_and_seed()
    serial = tuple(
        expand_assignments(serial_scorer, task.seed, config=task.config) for task in tasks
    )
    one_events: list[dict[str, Any]] = []
    one = parallel.run_expansion_modes(
        tasks, _Factory(), worker_count=1, progress_callback=one_events.append
    )
    four_events: list[dict[str, Any]] = []
    finished: list[int] = []
    four = parallel.run_expansion_modes(
        tasks,
        _Factory(),
        worker_count=4,
        progress_callback=four_events.append,
        result_callback=lambda item: finished.append(item.mode_index),
    )

    assert one.complete and four.complete
    assert one.completed_mode_ids == four.completed_mode_ids == (0, 1, 2, 3)
    assert [item.mode_index for item in one.outcomes] == [item.mode_index for item in four.outcomes]
    for expected, single, parallel_item in zip(serial, one.outcomes, four.outcomes, strict=True):
        _equivalent(expected, single.result)
        _equivalent(expected, parallel_item.result)
    for events in (one_events, four_events):
        by_mode: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            by_mode.setdefault(event["mode_index"], []).append(event)
        for mode_events in by_mode.values():
            previous = (-1, -1)
            for event in mode_events:
                current = (event["branch_id"], 0 if event["phase"] == "branch_started" else 1)
                assert current >= previous
                previous = current
    assert finished and sorted(finished) == [0, 1, 2, 3]
    assert len(four.worker_provenance) == 4
    assert all(item.rss_bytes_at_ready > 0 for item in four.worker_provenance)
    assert all(item.peak_rss_bytes >= item.rss_bytes_at_ready for item in four.worker_provenance)
    assert all(len(item.scorer_sha256) == 64 for item in four.worker_provenance)


def test_dispatch_order_is_caller_order_and_final_merge_is_mode_order():
    tasks = list(_tasks())
    tasks[0] = parallel.ModeTask(
        mode_index=0,
        seed=tasks[0].seed,
        config=ExpansionConfig(max_branches=100),
        candidate_branch_count=100,
        package_commit=tasks[0].package_commit,
        input_digest="mode-input-0",
        source_digest=STATIC_DIGEST,
    )
    # Caller priorities are retained as given; results are always mode sorted.
    priority_order = [tasks[3], tasks[0], tasks[2], tasks[1]]
    completion_order: list[int] = []
    ordered = parallel.run_expansion_modes(
        tasks,
        _Factory(delay_by_budget=True),
        worker_count=4,
        result_callback=lambda item: completion_order.append(item.mode_index),
    )
    priority_completion: list[int] = []
    result = parallel.run_expansion_modes(
        priority_order,
        _Factory(delay_by_budget=True),
        worker_count=4,
        result_callback=lambda item: priority_completion.append(item.mode_index),
    )
    assert ordered.complete and result.complete
    assert completion_order[0] != 0
    assert priority_completion[0] != 0
    assert tuple(item.mode_index for item in result.outcomes) == (0, 1, 2, 3)
    assert result.completed_mode_ids == (0, 1, 2, 3)
    for left, right in zip(ordered.outcomes, result.outcomes, strict=True):
        assert left.mode_index == right.mode_index
        _equivalent(left.result, right.result)


def test_budget_exhaustion_is_a_completed_mode_with_original_counters():
    tasks = _tasks(max_branches=1)
    result = parallel.run_expansion_modes(tasks, _Factory(), worker_count=1)
    assert result.complete
    assert all(item.result.budget_exhausted for item in result.outcomes)
    assert all(item.result.status == "budget_exhausted" for item in result.outcomes)
    assert result.summary["unvisited_branches"] > 0


def test_initializer_failure_preserves_all_modes_as_not_dispatched():
    tasks = _tasks()
    with pytest.raises(parallel.ParallelExpansionError, match="initialization failed") as caught:
        parallel.run_expansion_modes(tasks, _Factory(fail_init=True), worker_count=1)
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.not_dispatched_mode_ids == (0, 1, 2, 3)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_unexpected_worker_exit_keeps_every_submitted_mode_accounted(exit_code: int):
    tasks = _tasks()
    with pytest.raises(
        parallel.ParallelExpansionError, match="worker exited unexpectedly"
    ) as caught:
        parallel.run_expansion_modes(
            tasks, _Factory(crash_exit=exit_code), worker_count=1, deadline_s=10.0
        )
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert (
        caught.value.partial_result.failed_mode_ids or caught.value.partial_result.pending_mode_ids
    )


def test_mode_exception_reports_failed_and_pending_modes():
    tasks = _tasks()
    with pytest.raises(parallel.ParallelExpansionError, match="mode 0 failed") as caught:
        parallel.run_expansion_modes(tasks, _Factory(fail_mode=True), worker_count=1)
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.failed_mode_ids == (0,)


def test_deadline_during_worker_startup_reports_unsent_modes():
    tasks = _tasks()
    with pytest.raises(parallel.ParallelExpansionError, match="deadline exceeded") as caught:
        parallel.run_expansion_modes(
            tasks, _Factory(slow_init_s=0.5), worker_count=1, deadline_s=0.1
        )
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.not_dispatched_mode_ids == (0, 1, 2, 3)


def test_callback_exception_cancels_owned_pool_and_keeps_dispositions():
    tasks = _tasks()

    def fail_callback(_event: dict[str, Any]) -> None:
        raise RuntimeError("intentional callback failure")

    with pytest.raises(parallel.ParallelExpansionError, match="progress callback failed") as caught:
        parallel.run_expansion_modes(
            tasks, _Factory(), worker_count=4, progress_callback=fail_callback
        )
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.pending_mode_ids
    _assert_pids_reaped(caught.value.partial_result)


@pytest.mark.parametrize("mismatch", ["package", "source"])
def test_factory_and_mode_source_pins_are_checked_before_dispatch(mismatch: str):
    tasks = _tasks()
    factory = _Factory()
    if mismatch == "package":
        tasks = tuple(replace(task, package_commit="0" * 40) for task in tasks)
        message = "loaded package commit differs"
    else:
        factory = _Factory(source_digest="different-static-inputs")
        message = "source_digest differs"
    with pytest.raises(parallel.ParallelExpansionError, match=message) as caught:
        parallel.run_expansion_modes(tasks, factory, worker_count=1)
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.not_dispatched_mode_ids == (0, 1, 2, 3)


def test_deadline_during_mode_execution_reaps_workers_and_preserves_pending_ids():
    tasks = list(_tasks())
    tasks[0] = replace(tasks[0], config=ExpansionConfig(max_branches=100))
    with pytest.raises(parallel.ParallelExpansionError, match="deadline exceeded") as caught:
        parallel.run_expansion_modes(
            tasks, _Factory(delay_mode_s=8.0), worker_count=1, deadline_s=3.0
        )
    _assert_complete_partition(caught.value, {0, 1, 2, 3})
    assert caught.value.partial_result.pending_mode_ids
    assert caught.value.partial_result.worker_provenance
    _assert_pids_reaped(caught.value.partial_result)


def test_partial_process_start_failure_reaps_started_children_and_keeps_modes(monkeypatch):
    real_context = mp.get_context("spawn")
    started_pids: list[int] = []
    call_count = 0

    class InterceptedProcess:
        def __init__(self, process):
            self.process = process

        def start(self):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("intentional second start failure")
            self.process.start()
            assert self.process.pid is not None
            started_pids.append(self.process.pid)

        def __getattr__(self, name):
            return getattr(self.process, name)

    class ContextProxy:
        def Queue(self, *args, **kwargs):
            return real_context.Queue(*args, **kwargs)

        def Process(self, *args, **kwargs):
            return InterceptedProcess(real_context.Process(*args, **kwargs))

    monkeypatch.setattr(parallel.mp, "get_context", lambda _method: ContextProxy())
    with pytest.raises(parallel.ParallelExpansionError, match="process startup failed") as caught:
        parallel.run_expansion_modes(_tasks(), _Factory(), worker_count=4)
    assert caught.value.partial_result.not_dispatched_mode_ids == (0, 1, 2, 3)
    assert caught.value.partial_result.pending_mode_ids == ()
    assert len(started_pids) == 1, caught.value.cause_text
    with pytest.raises(ProcessLookupError):
        os.kill(started_pids[0], 0)
