from __future__ import annotations

import queue
import time
from typing import Any, cast

import pytest

from accuracy.test_screened_search import _scenario
from maritime.search.parallel_screened import (
    CandidateAudit,
    ParallelScreenedCandidatePool,
    ParallelScreenedError,
    _Chunk,
    _Msg,
)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_screened_chunks_preserve_finite_domain_and_exact_winner(workers: int) -> None:
    scorer, points, _ = _scenario(True)
    expected = [scorer.evaluate(point.position_xy_m) for point in points]
    expected_hypotheses = [
        item.hypothesis
        for item in expected
        if item.status == "scored" and item.hypothesis is not None
    ]
    expected_score = min(item.score for item in expected_hypotheses)
    expected_ties = {
        (item.east_m, item.north_m)
        for item in expected_hypotheses
        if abs(item.score - expected_score) <= 1e-8
    }

    with ParallelScreenedCandidatePool(
        scorer.catalog,
        scorer.vector_map,
        workers=workers,
        chunk_size=2,
        max_in_flight_chunks=workers,
        memory_budget_bytes=4 * 1024**3,
    ) as pool:
        chunks = list(
            pool.iter_chunks(
                scorer.measurements,
                scorer.config,
                points,
                mode="screened",
                complete_candidate_count=len(points),
            )
        )

    audits = [audit for chunk in chunks for audit in chunk.candidates]
    hypotheses = [hypothesis for chunk in chunks for _, hypothesis in chunk.hypotheses]
    assert [audit.candidate_id for audit in audits] == [point.candidate_id for point in points]
    assert all(
        chunk.request_complete is (index == len(chunks) - 1) for index, chunk in enumerate(chunks)
    )
    assert chunks[-1].finite_candidate_set is False  # unknown coverage is unresolved
    assert sum(chunk.candidate_count for chunk in chunks) == len(points)
    assert sum(chunk.exact_evaluation_count for chunk in chunks) + sum(
        chunk.pruned_count for chunk in chunks
    ) == len(points)
    assert any(audit.action == "pruned" for audit in audits)
    assert (
        next(audit for audit in audits if audit.candidate_id == "land").action
        == "exact_invalid_water"
    )
    assert (
        next(audit for audit in audits if audit.candidate_id == "unknown").action
        == "exact_unresolved"
    )
    actual_score = min(item.score for item in hypotheses)
    actual_ties = {
        (item.east_m, item.north_m) for item in hypotheses if abs(item.score - actual_score) <= 1e-8
    }
    assert actual_score == pytest.approx(expected_score, abs=1e-12)
    assert actual_ties == expected_ties


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_exact_mode_matches_serial_outcome_categories_and_scores(workers: int) -> None:
    scorer, points, _ = _scenario(True)
    direct = [scorer.evaluate(point.position_xy_m) for point in points]
    with ParallelScreenedCandidatePool(
        scorer.catalog,
        scorer.vector_map,
        workers=workers,
        chunk_size=2,
        max_in_flight_chunks=workers,
        memory_budget_bytes=4 * 1024**3,
    ) as pool:
        chunks = list(
            pool.iter_chunks(
                scorer.measurements,
                scorer.config,
                iter(points),
                mode="exact",
                complete_candidate_count=len(points),
            )
        )

    audits = [audit for chunk in chunks for audit in chunk.candidates]
    assert [audit.input_index for audit in audits] == list(range(len(points)))
    assert [audit.model_status for audit in audits] == [item.status for item in direct]
    assert sum(chunk.pruned_count for chunk in chunks) == 0
    assert sum(chunk.exact_evaluation_count for chunk in chunks) == len(points)
    actual = {audit.candidate_id: audit.score for audit in audits}
    for point, evaluation in zip(points, direct, strict=True):
        assert actual[point.candidate_id] == (
            None
            if evaluation.hypothesis is None
            else pytest.approx(evaluation.hypothesis.score, abs=1e-12)
        )


def test_incumbent_snapshots_are_position_only_and_stale_safe() -> None:
    scorer, points, _ = _scenario(True)
    outputs = []
    for workers in (1, 2):
        with ParallelScreenedCandidatePool(
            scorer.catalog,
            scorer.vector_map,
            workers=workers,
            chunk_size=1,
            max_in_flight_chunks=workers,
            memory_budget_bytes=4 * 1024**3,
        ) as pool:
            chunks = list(
                pool.iter_chunks(
                    scorer.measurements,
                    scorer.config,
                    points,
                    mode="screened",
                    complete_candidate_count=len(points),
                )
            )
        audits = [audit for chunk in chunks for audit in chunk.candidates]
        hypotheses = [hyp for chunk in chunks for _, hyp in chunk.hypotheses]
        outputs.append((audits, hypotheses, chunks))
    for audits, hypotheses, chunks in outputs:
        assert [item.input_index for item in audits] == list(range(len(points)))
        assert sum(item.candidate_count for item in chunks) == len(points)
        assert sum(item.pruned_count + item.exact_evaluation_count for item in chunks) == len(
            points
        )
        for audit in audits:
            if audit.action == "pruned":
                assert audit.lower_bound_supported is True
                assert audit.lower_bound is not None
                assert audit.visible_group_count_k is not None
        assert min(h.score for h in hypotheses) == pytest.approx(
            min(
                scorer.evaluate(p.position_xy_m).hypothesis.score
                for p in points
                if scorer.evaluate(p.position_xy_m).hypothesis is not None
            ),
            abs=1e-12,
        )
    bests = [
        min((hyp.score, hyp.east_m, hyp.north_m) for hyp in hypotheses)
        for _, hypotheses, _ in outputs
    ]
    assert bests[0] == pytest.approx(bests[1], abs=1e-12)


def test_early_close_invalidates_pool_so_stale_chunks_cannot_cross_requests() -> None:
    scorer, points, _ = _scenario(True)
    pool = ParallelScreenedCandidatePool(
        scorer.catalog,
        scorer.vector_map,
        workers=2,
        chunk_size=1,
        max_in_flight_chunks=2,
        memory_budget_bytes=4 * 1024**3,
    )
    stream = pool.iter_chunks(
        scorer.measurements,
        scorer.config,
        points * 3,
        mode="exact",
    )
    first = next(stream)
    assert first.chunk_index == 0
    stream.close()
    with pytest.raises(RuntimeError, match="closed or failed"):
        list(pool.iter_chunks(scorer.measurements, scorer.config, points, mode="exact"))


def test_empty_input_is_complete_without_a_false_finite_certificate() -> None:
    scorer, _, _ = _scenario(True)
    with ParallelScreenedCandidatePool(
        scorer.catalog,
        scorer.vector_map,
        workers=1,
        chunk_size=2,
        max_in_flight_chunks=1,
        memory_budget_bytes=4 * 1024**3,
    ) as pool:
        chunks = list(
            pool.iter_chunks(
                scorer.measurements,
                scorer.config,
                (),
                mode="screened",
                complete_candidate_count=0,
            )
        )
    assert chunks == []


def test_same_chunk_incumbent_schedule_is_deterministic() -> None:
    scorer, points, _ = _scenario(True)
    snapshots = []
    for _ in range(2):
        with ParallelScreenedCandidatePool(
            scorer.catalog,
            scorer.vector_map,
            workers=2,
            chunk_size=1,
            max_in_flight_chunks=2,
            memory_budget_bytes=4 * 1024**3,
        ) as pool:
            chunks = list(
                pool.iter_chunks(
                    scorer.measurements,
                    scorer.config,
                    points,
                    mode="screened",
                    complete_candidate_count=len(points),
                )
            )
        snapshots.append(
            [
                (
                    audit.candidate_id,
                    audit.action,
                    audit.score,
                    audit.lower_bound,
                    audit.visible_group_count_k,
                    audit.model_status,
                )
                for chunk in chunks
                for audit in chunk.candidates
            ]
        )
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize("exit_mode", ["zero", "nonzero"])
def test_worker_exit_fails_promptly_with_outstanding_count(exit_mode: str) -> None:
    from maritime.search.parallel_screened import ParallelScreenedError

    scorer, points, _ = _scenario(True)
    pool = ParallelScreenedCandidatePool(
        scorer.catalog,
        scorer.vector_map,
        workers=1,
        chunk_size=1,
        max_in_flight_chunks=1,
        memory_budget_bytes=4 * 1024**3,
        request_timeout_s=3.0,
    )
    if exit_mode == "zero":
        pool._tasks.put(None)
    else:
        pool._procs[0].terminate()
        pool._procs[0].join(timeout=2)
    with pytest.raises(ParallelScreenedError) as error:
        list(pool.iter_chunks(scorer.measurements, scorer.config, points, mode="exact"))
    assert error.value.completed_count == 0
    assert error.value.failed_count == 0
    assert error.value.outstanding_count == 1


class _FakeTaskQueue:
    def __init__(self, *, fail_put: bool = False) -> None:
        self.tasks = []
        self.fail_put = fail_put

    def put(self, task, timeout: float) -> None:
        del timeout
        if self.fail_put:
            raise queue.Full
        self.tasks.append(task)


class _FakeResultQueue:
    def __init__(self, messages, *, delay_s: float = 0.0) -> None:
        self.messages = list(messages)
        self.delay_s = delay_s

    def get(self, timeout: float):
        del timeout
        if self.delay_s:
            time.sleep(self.delay_s)
            self.delay_s = 0.0
        if not self.messages:
            raise queue.Empty
        return self.messages.pop(0)


def _synthetic_chunk(index: int, candidate_id: str) -> _Chunk:
    audit = CandidateAudit(
        candidate_id,
        index,
        (float(index), 0.0),
        "exact_invalid_water",
        None,
        None,
        None,
        None,
        "invalid_water",
    )
    return _Chunk(index, (audit,), (), 0, 1, 1, 0, 0, 0, 0, 0.0, 0.0)


def _synthetic_message(index: int, candidate_id: str, *, error: str | None = None) -> _Msg:
    return _Msg(
        "chunk",
        0,
        9000 + index,
        0,
        index,
        None if error else _synthetic_chunk(index, candidate_id),
        error=error,
    )


def _coordinator_only_pool(
    result_queue, *, request_timeout_s: float | None = None, fail_put: bool = False
):
    pool = cast(Any, object.__new__(ParallelScreenedCandidatePool))
    pool.window = 3
    pool.chunk_size = 1
    pool.request_timeout_s = request_timeout_s
    pool._active = False
    pool._startup = 0.0
    pool._rssmap = {}
    pool._procs = []
    pool._tasks = _FakeTaskQueue(fail_put=fail_put)
    pool._results = result_queue
    pool._timings = None
    pool._memory = lambda estimate: None
    pool._abort = lambda: setattr(pool, "_failed", True)
    return pool


def test_out_of_order_results_are_partitioned_on_worker_failure() -> None:
    scorer, points, _ = _scenario(True)
    messages = [
        _synthetic_message(2, points[2].candidate_id),
        _synthetic_message(1, points[1].candidate_id, error="worker evaluation failed"),
        _synthetic_message(0, points[0].candidate_id),
    ]
    pool = _coordinator_only_pool(_FakeResultQueue(messages))
    stream = pool._request(
        scorer.measurements,
        scorer.config,
        points[:3],
        "exact",
        3,
        1,
    )
    with pytest.raises(ParallelScreenedError) as error:
        list(stream)
    disposition = error.value.disposition
    assert disposition.submitted_candidate_ids == tuple(p.candidate_id for p in points[:3])
    assert disposition.committed_candidate_ids == (points[0].candidate_id,)
    assert disposition.failed_candidate_ids == (points[1].candidate_id,)
    assert disposition.pending_candidate_ids == (points[2].candidate_id,)
    assert disposition.outstanding_candidate_ids == ()
    assert error.value.completed_count == 1
    assert error.value.failed_count == 1
    assert error.value.pending_count == 1


def test_timeout_reports_completed_pending_and_outstanding_ids() -> None:
    scorer, points, _ = _scenario(True)
    pool = _coordinator_only_pool(
        _FakeResultQueue([_synthetic_message(2, points[2].candidate_id)], delay_s=0.05),
        request_timeout_s=0.01,
    )
    stream = pool._request(
        scorer.measurements,
        scorer.config,
        points[:3],
        "exact",
        3,
        1,
    )
    with pytest.raises(ParallelScreenedError) as error:
        list(stream)
    disposition = error.value.disposition
    assert disposition.committed_candidate_ids == ()
    assert disposition.pending_candidate_ids == (points[2].candidate_id,)
    assert disposition.outstanding_candidate_ids == tuple(p.candidate_id for p in points[:2])
    assert disposition.failed_candidate_ids == ()


def test_cancellation_keeps_pending_and_outstanding_id_ledger() -> None:
    scorer, points, _ = _scenario(True)
    messages = [
        _synthetic_message(1, points[1].candidate_id),
        _synthetic_message(0, points[0].candidate_id),
    ]
    pool = _coordinator_only_pool(_FakeResultQueue(messages))
    stream = pool._request(
        scorer.measurements,
        scorer.config,
        points[:3],
        "exact",
        3,
        1,
    )
    first = next(stream)
    assert first.chunk_index == 0
    stream.close()
    disposition = pool.last_disposition
    assert disposition.committed_candidate_ids == (points[0].candidate_id,)
    assert disposition.pending_candidate_ids == (points[1].candidate_id,)
    assert disposition.outstanding_candidate_ids == (points[2].candidate_id,)


def test_queue_submission_failure_identifies_not_dispatched_ids() -> None:
    scorer, points, _ = _scenario(True)
    pool = _coordinator_only_pool(
        _FakeResultQueue([]),
        request_timeout_s=0.01,
        fail_put=True,
    )
    stream = pool._request(
        scorer.measurements,
        scorer.config,
        points[:2],
        "exact",
        2,
        1,
    )
    with pytest.raises(ParallelScreenedError) as error:
        list(stream)
    disposition = error.value.disposition
    assert disposition.submitted_candidate_ids == ()
    assert disposition.not_dispatched_candidate_ids == (points[0].candidate_id,)
    assert disposition.committed_candidate_ids == ()
    assert disposition.pending_candidate_ids == ()
    assert disposition.failed_candidate_ids == ()
    assert disposition.outstanding_candidate_ids == ()
