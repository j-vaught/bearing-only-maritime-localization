from __future__ import annotations

import multiprocessing as mp
import os
import threading
from typing import cast

import numpy as np
import pytest
from shapely.geometry import box

from maritime.schemas.inputs_v2 import CatalogV2
from maritime.search.config import SearchConfig
from maritime.search.parallel_candidates import (
    CandidatePoolError,
    CandidatePositionInput,
    PersistentCandidatePool,
    _MapPayload,
    _Task,
    _worker_main,
)
from maritime.search.scoring import CandidateScorer, score_candidate_positions

from .test_architecture_regressions import _catalog, _map, _measurements, _origin_xy, _record


def _exit_after_unpickle(exit_code: int) -> None:
    os._exit(exit_code)


class _ExitOnUnpickle:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def __reduce__(self):
        return _exit_after_unpickle, (self.exit_code,)


def _consume_request(pool, measurements, config, candidates, outcome) -> None:
    try:
        list(pool.iter_results(measurements, config, candidates))
    except BaseException as exc:
        outcome["error"] = exc
    else:
        outcome["error"] = None


def _run_bounded_request(pool, measurements, config, candidates, *, timeout_s: float = 6.0):
    outcome: dict[str, BaseException | None] = {}
    thread = threading.Thread(
        target=_consume_request,
        args=(pool, measurements, config, candidates, outcome),
        daemon=True,
    )
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        pool.close()
        thread.join(3.0)
        pytest.fail("candidate pool request exceeded the watchdog deadline")
    return outcome["error"]


def _fixture(use_types: bool = True):
    east, north = _origin_xy()
    vector_map = _map(
        land=box(east + 20.0, north - 20.0, east + 40.0, north + 20.0),
        coverage=box(east - 20_000.0, north - 20_000.0, east + 20_000.0, north + 20_000.0),
        unknown=box(east + 45.0, north - 10.0, east + 55.0, north + 10.0),
    )
    measurements = _measurements(
        (0.0, 0.05, "vessel"),
        (90.0, 0.8, "physical_aid"),
        (180.0, 4.0, "mystery craft"),
    )
    catalog = _catalog(
        _record("north", 0.0, 1000.0, kind="vessel"),
        _record("east", 90.0, 1200.0, kind="physical_aid"),
        _record("south", 180.0, 1500.0, kind="vessel"),
    )
    config = SearchConfig(max_range_m=5000.0, use_types=use_types)
    positions = (
        _origin_xy(),
        (east + 30.0, north),
        (east + 50.0, north),
        (east + 21_000.0, north),
        _origin_xy(),
        (east - 200.0, north + 100.0),
    )
    candidates = tuple(
        CandidatePositionInput(f"candidate-{i}", pos) for i, pos in enumerate(positions)
    )
    return measurements, catalog, vector_map, config, positions, candidates


def _assert_same_run(actual, expected) -> None:
    assert actual.candidate_count == expected.candidate_count
    assert actual.outcome_counts == expected.outcome_counts
    assert actual.hypotheses == expected.hypotheses
    assert actual.localization.decision_status == expected.localization.decision_status
    assert actual.localization.search_status == expected.localization.search_status
    assert actual.localization.certificate_scope == expected.localization.certificate_scope
    assert actual.localization.budget_limit == expected.localization.budget_limit
    assert actual.localization.budget_used == expected.localization.budget_used
    assert actual.localization.report_count == expected.localization.report_count
    assert actual.localization.objective_version == expected.localization.objective_version


@pytest.mark.parametrize("workers", [1, 2, 4])
@pytest.mark.parametrize("use_types", [False, True])
def test_pool_score_matches_serial_deduplication_and_order(workers: int, use_types: bool) -> None:
    measurements, catalog, vector_map, config, positions, candidates = _fixture(use_types)
    serial = score_candidate_positions(
        CandidateScorer(measurements, catalog, vector_map, config),
        positions,
        certificate_scope="finite_candidate_set",
    )
    with PersistentCandidatePool(
        catalog,
        vector_map,
        workers=workers,
        batch_size=2,
        max_in_flight_batches=workers,
        memory_budget_bytes=2_000_000_000,
    ) as pool:
        result = pool.score(
            measurements,
            config,
            candidates,
            certificate_scope="finite_candidate_set",
        )
        _assert_same_run(result, serial)
        timings = pool.last_timings
        assert timings.input_count == len(candidates)
        assert timings.evaluation_count == 5
        assert timings.duplicate_position_count == 1
        assert len(timings.worker_rss_bytes) == workers
        assert all(rss > 0 for _, rss in timings.worker_rss_bytes)


def test_record_iterator_keeps_duplicate_records_and_stable_ids() -> None:
    measurements, catalog, vector_map, config, positions, candidates = _fixture()
    with PersistentCandidatePool(
        catalog,
        vector_map,
        workers=2,
        batch_size=2,
        memory_budget_bytes=2_000_000_000,
    ) as pool:
        results = list(pool.iter_results(measurements, config, candidates))
    assert [item.candidate_id for item in results] == [item.candidate_id for item in candidates]
    assert [item.sequence for item in results] == list(range(len(candidates)))
    assert [item.position_xy for item in results] == list(positions)
    assert results[0].evaluation.status == results[4].evaluation.status
    assert results[0].evaluation.hypothesis == results[4].evaluation.hypothesis
    assert results[0].evaluation.costs is not None
    assert results[4].evaluation.costs is not None
    np.testing.assert_array_equal(
        results[0].evaluation.costs.matched_costs,
        results[4].evaluation.costs.matched_costs,
    )
    assert np.any(results[0].evaluation.costs.matched_costs < 0.0)


def test_candidate_limit_matches_serial_budget_and_reports_raw_duplicates() -> None:
    measurements, catalog, vector_map, config, positions, candidates = _fixture()
    candidates = (
        candidates[0],
        candidates[1],
        CandidatePositionInput("duplicate", positions[0]),
        *candidates[2:],
    )
    positions = tuple(candidate.position_xy for candidate in candidates)
    serial = score_candidate_positions(
        CandidateScorer(measurements, catalog, vector_map, config),
        positions,
        candidate_limit=2,
    )
    with PersistentCandidatePool(
        catalog,
        vector_map,
        workers=2,
        batch_size=1,
        memory_budget_bytes=2_000_000_000,
    ) as pool:
        result = pool.score(measurements, config, candidates, candidate_limit=2)
        _assert_same_run(result, serial)
        assert pool.last_timings.duplicate_position_count == 1
        assert pool.last_timings.input_count == 4
        assert pool.last_timings.evaluation_count == 2


def test_unresolved_model_outcome_is_preserved() -> None:
    measurements, catalog, vector_map, config, _, _ = _fixture()
    unsupported = _record(
        "unsupported-model",
        270.0,
        1800.0,
        metadata=(("length_m", 50.0),),
    )
    catalog = CatalogV2(
        records=(*catalog.records, unsupported),
        timestamp_utc=catalog.timestamp_utc,
        source=catalog.source,
        catalog_version=catalog.catalog_version,
        catalog_id=catalog.catalog_id,
    )
    candidate = CandidatePositionInput("unresolved", _origin_xy())
    serial = CandidateScorer(measurements, catalog, vector_map, config).evaluate(
        candidate.position_xy
    )
    with PersistentCandidatePool(
        catalog,
        vector_map,
        workers=2,
        batch_size=1,
        memory_budget_bytes=2_000_000_000,
    ) as pool:
        parallel = list(pool.iter_results(measurements, config, (candidate,)))[0].evaluation
    assert serial.status == parallel.status == "unresolved_model"
    assert serial.reason == parallel.reason


def test_uneven_candidate_batches_merge_to_serial_sequence() -> None:
    measurements, catalog, vector_map, config, _, _ = _fixture()
    east, north = _origin_xy()
    positions = tuple(
        (east + float(index * 137), north + float((index % 7) * 61)) for index in range(36)
    )
    candidates = tuple(
        CandidatePositionInput(index, point) for index, point in enumerate(positions)
    )
    serial = score_candidate_positions(
        CandidateScorer(measurements, catalog, vector_map, config), positions
    )
    with PersistentCandidatePool(
        catalog,
        vector_map,
        workers=4,
        batch_size=1,
        max_in_flight_batches=4,
        memory_budget_bytes=2_000_000_000,
    ) as pool:
        parallel = pool.score(measurements, config, candidates)
    _assert_same_run(parallel, serial)
    assert parallel.candidate_count == 36
    assert pool.last_timings.evaluation_count == 36


def test_worker_reports_scorer_setup_exception_with_candidate_identity() -> None:
    measurements, catalog, vector_map, config, _, candidates = _fixture()
    context = mp.get_context("spawn")
    task_queue = context.Queue()
    result_queue = context.Queue()
    payload = _MapPayload(
        vector_map.crs.to_string(),
        vector_map.land_geometry.wkb,
        vector_map._coverage.wkb,
        vector_map._unknown.wkb,
    )
    process = context.Process(
        target=_worker_main,
        args=(0, catalog, payload, task_queue, result_queue),
    )
    process.start()
    try:
        ready = result_queue.get(timeout=20)
        assert ready.kind == "ready" and ready.error is None
        task_queue.put(
            _Task(
                "broken-request",
                0,
                0,
                (candidates[0],),
                measurements,
                cast(SearchConfig, "invalid-config"),
            )
        )
        message = result_queue.get(timeout=20)
        assert message.kind == "batch"
        assert message.entries[0].candidate_id == candidates[0].candidate_id
        assert message.entries[0].result is None
        assert "TypeError" in message.entries[0].error
        task_queue.put(None)
        assert result_queue.get(timeout=20).kind == "stopped"
    finally:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        task_queue.close()
        result_queue.close()
    assert process.exitcode == 0


def test_pool_rejects_memory_estimate_before_request() -> None:
    _, catalog, vector_map, _, _, _ = _fixture()
    with pytest.raises(MemoryError, match="measured worker RSS"):
        PersistentCandidatePool(
            catalog,
            vector_map,
            workers=1,
            batch_size=8,
            max_in_flight_batches=1,
            memory_budget_bytes=50_000_000,
        )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_unexpected_worker_exit_is_bounded_and_reports_counts(exit_code: int) -> None:
    measurements, catalog, vector_map, config, _, candidates = _fixture()
    pool = PersistentCandidatePool(
        catalog,
        vector_map,
        workers=1,
        batch_size=1,
        max_in_flight_batches=1,
        memory_budget_bytes=2_000_000_000,
    )
    owned_processes = tuple(pool._processes)
    pool._task_queue.put(_ExitOnUnpickle(exit_code))
    error = _run_bounded_request(pool, measurements, config, candidates)
    assert isinstance(error, CandidatePoolError)
    assert error.completed_count == 0
    assert error.failed_count == 0
    assert error.outstanding_count >= 1
    assert "exited unexpectedly" in str(error.__cause__)
    assert all(not process.is_alive() for process in owned_processes)
    with pytest.raises(RuntimeError, match="unusable"):
        pool.iter_results(measurements, config, candidates)


def test_worker_stopped_message_is_bounded_and_reaps_workers() -> None:
    measurements, catalog, vector_map, config, _, candidates = _fixture()
    pool = PersistentCandidatePool(
        catalog,
        vector_map,
        workers=1,
        batch_size=1,
        max_in_flight_batches=1,
        memory_budget_bytes=2_000_000_000,
    )
    owned_processes = tuple(pool._processes)
    pool._task_queue.put(object())
    error = _run_bounded_request(pool, measurements, config, candidates)
    assert isinstance(error, CandidatePoolError)
    assert error.completed_count == 0
    assert error.failed_count == 0
    assert error.outstanding_count >= 1
    assert "invalid task record" in str(error.__cause__)
    assert all(not process.is_alive() for process in owned_processes)
    with pytest.raises(RuntimeError, match="unusable"):
        pool.iter_results(measurements, config, candidates)


def test_early_iterator_close_reaps_workers_and_disallows_reuse() -> None:
    measurements, catalog, vector_map, config, _, _ = _fixture()
    positions = tuple((float(index * 25), float(index * 13)) for index in range(40))
    candidates = tuple(
        CandidatePositionInput(index, point) for index, point in enumerate(positions)
    )
    pool = PersistentCandidatePool(
        catalog,
        vector_map,
        workers=2,
        batch_size=1,
        max_in_flight_batches=4,
        memory_budget_bytes=2_000_000_000,
    )
    owned_processes = tuple(pool._processes)
    iterator = pool.iter_results(measurements, config, candidates)
    first = next(iterator)
    assert first.sequence == 0
    iterator.close()
    assert pool._closed and pool._failed
    assert all(not process.is_alive() for process in owned_processes)
    with pytest.raises(RuntimeError, match="unusable"):
        pool.iter_results(measurements, config, candidates)
