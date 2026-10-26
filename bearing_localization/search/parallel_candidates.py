"""Persistent process execution for exact fixed-candidate CPU scoring."""

from __future__ import annotations

import multiprocessing as mp
import pickle
import queue
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from shapely import from_wkb

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import Hypothesis, LocalizationResult
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .config import SearchConfig
from .scoring import (
    _STATUS_ORDER,
    CandidateEvaluation,
    CandidateScorer,
    CandidateSearchRun,
    CandidateStatus,
)


@dataclass(frozen=True, slots=True)
class CandidatePositionInput:
    """Stable caller ID paired with one fixed projected candidate position."""

    candidate_id: str | int
    position_xy: tuple[float, float]

    def __post_init__(self) -> None:
        _validate_candidate_id(self.candidate_id)
        if (
            not isinstance(self.position_xy, tuple)
            or len(self.position_xy) != 2
            or not all(
                isinstance(value, (int, float)) and isfinite(value) for value in self.position_xy
            )
        ):
            raise ValueError("position_xy must be a pair of finite projected coordinates.")


@dataclass(frozen=True, slots=True)
class CandidatePositionResult:
    """One exact evaluation associated with its stable input ID and sequence."""

    candidate_id: str | int
    position_xy: tuple[float, float]
    evaluation: CandidateEvaluation
    sequence: int


@dataclass(frozen=True, slots=True)
class CandidatePoolTimings:
    """Measured setup and work totals. Component times can overlap."""

    pool_startup_s: float
    scorer_setup_s: float
    ipc_s: float
    worker_evaluation_s: float
    elapsed_s: float
    input_count: int
    evaluation_count: int
    duplicate_position_count: int
    worker_rss_bytes: tuple[tuple[int, int], ...]
    estimated_memory_bytes: int


class CandidatePoolError(RuntimeError):
    """Worker or feeder failure with completed, failed and outstanding counts."""

    def __init__(
        self,
        message: str,
        *,
        completed_count: int,
        failed_count: int,
        outstanding_count: int,
        failed_candidate_ids: Sequence[str | int] = (),
    ) -> None:
        super().__init__(message)
        self.completed_count = completed_count
        self.failed_count = failed_count
        self.outstanding_count = outstanding_count
        self.failed_candidate_ids = tuple(failed_candidate_ids)


@dataclass(frozen=True, slots=True)
class _MapPayload:
    crs: str
    land_wkb: bytes
    coverage_wkb: bytes | None
    unknown_wkb: bytes | None


@dataclass(frozen=True, slots=True)
class _Task:
    request_id: str
    batch_id: int
    start_sequence: int
    candidates: tuple[CandidatePositionInput, ...]
    measurements: MeasurementSetV2
    config: SearchConfig


@dataclass(frozen=True, slots=True)
class _Entry:
    candidate_id: str | int
    result: CandidatePositionResult | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _Message:
    kind: Literal["ready", "batch", "stopped"]
    worker_id: int
    pid: int
    rss_bytes: int
    entries: tuple[_Entry, ...] = ()
    batch_id: int = -1
    scorer_setup_s: float = 0.0
    evaluation_s: float = 0.0
    error: str | None = None


def _worker_main(
    worker_id: int,
    catalog: CatalogV2,
    map_payload: _MapPayload,
    task_queue: Any,
    result_queue: Any,
) -> None:
    """Spawn target; imports and mutable scorer state remain worker-local."""
    try:
        vector_map = VectorMap(
            from_wkb(map_payload.land_wkb),
            crs=map_payload.crs,
            coverage_geometry=from_wkb(map_payload.coverage_wkb)
            if map_payload.coverage_wkb
            else None,
            unknown_geometry=from_wkb(map_payload.unknown_wkb) if map_payload.unknown_wkb else None,
        )
        result_queue.put(_Message("ready", worker_id, _pid(), _rss_bytes()))
    except BaseException as exc:
        result_queue.put(_Message("ready", worker_id, _pid(), _rss_bytes(), error=_error_text(exc)))
        return

    current_request: str | None = None
    scorer: CandidateScorer | None = None
    try:
        while True:
            task = task_queue.get()
            if task is None:
                break
            if not isinstance(task, _Task):
                raise TypeError("worker received an invalid task record.")
            entries: list[_Entry] = []
            setup_s = evaluation_s = 0.0
            try:
                if current_request != task.request_id:
                    # Drop prior request measurements before constructing the
                    # replacement scorer, keeping one request resident.
                    scorer = None
                    current_request = None
                    started = perf_counter()
                    scorer = CandidateScorer(task.measurements, catalog, vector_map, task.config)
                    setup_s = perf_counter() - started
                    current_request = task.request_id
                if scorer is None:
                    raise RuntimeError("worker scorer was not initialized.")
                for offset, candidate in enumerate(task.candidates):
                    started = perf_counter()
                    try:
                        result = scorer.evaluate(candidate.position_xy)
                        entries.append(
                            _Entry(
                                candidate.candidate_id,
                                CandidatePositionResult(
                                    candidate.candidate_id,
                                    candidate.position_xy,
                                    result,
                                    task.start_sequence + offset,
                                ),
                            )
                        )
                    except BaseException as exc:
                        entries.append(_Entry(candidate.candidate_id, None, _error_text(exc)))
                    evaluation_s += perf_counter() - started
            except BaseException as exc:
                # Return one explicit failure row per input so accounting never
                # mistakes a scorer setup failure for completed work.
                entries = [
                    _Entry(item.candidate_id, None, _error_text(exc)) for item in task.candidates
                ]
                scorer = None
                current_request = None
            result_queue.put(
                _Message(
                    "batch",
                    worker_id,
                    _pid(),
                    _rss_bytes(),
                    tuple(entries),
                    task.batch_id,
                    setup_s,
                    evaluation_s,
                )
            )
    except BaseException as exc:
        result_queue.put(
            _Message("stopped", worker_id, _pid(), _rss_bytes(), error=_error_text(exc))
        )
        return
    result_queue.put(_Message("stopped", worker_id, _pid(), _rss_bytes()))


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _pid() -> int:
    import os

    return os.getpid()


def _rss_bytes(pid: int | None = None) -> int:
    """Read resident bytes from procfs, returning zero if the process disappeared."""
    import os

    target = pid if pid is not None else os.getpid()
    try:
        fields = Path(f"/proc/{target}/statm").read_text().split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, IndexError, ValueError):
        return 0


def _validate_candidate_id(candidate_id: str | int) -> None:
    if type(candidate_id) is int:
        return
    if isinstance(candidate_id, str) and candidate_id.strip():
        return
    raise ValueError("candidate_id must be a nonempty string or integer.")


def _position_key(position_xy: tuple[float, float]) -> tuple[float, float]:
    return round(float(position_xy[0]), 9), round(float(position_xy[1]), 9)


class PersistentCandidatePool:
    """Persistent spawn-based CPU pool for exact fixed-position evaluation.

    Each process owns a private map and catalog, and replaces its scorer on
    every request so old measurements are not retained. ``iter_results``
    evaluates every input record; ``score`` applies serial deduplication and
    budget behavior. Memory sizes are admission estimates, supplemented by
    measured worker resident set size after startup and each batch.
    """

    def __init__(
        self,
        catalog: CatalogV2,
        vector_map: VectorMap,
        *,
        workers: int,
        max_in_flight_batches: int | None = None,
        batch_size: int = 8,
        memory_budget_bytes: int,
        start_method: Literal["spawn"] = "spawn",
    ) -> None:
        if not isinstance(catalog, CatalogV2):
            raise TypeError("catalog must be a CatalogV2 instance.")
        if not isinstance(vector_map, VectorMap):
            raise TypeError("vector_map must be a VectorMap instance.")
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer.")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer.")
        window = max_in_flight_batches if max_in_flight_batches is not None else 2 * workers
        if type(window) is not int or window < workers:
            raise ValueError("max_in_flight_batches must be at least workers.")
        if type(memory_budget_bytes) is not int or memory_budget_bytes < 1:
            raise ValueError("memory_budget_bytes must be a positive integer.")
        if start_method != "spawn":
            raise ValueError("start_method must be 'spawn'.")

        self.catalog = catalog
        self.batch_size = batch_size
        self.workers = workers
        self.max_in_flight_batches = window
        self.memory_budget_bytes = memory_budget_bytes
        self._map_payload = _map_payload(vector_map)
        static_size = len(
            pickle.dumps((catalog, self._map_payload), protocol=pickle.HIGHEST_PROTOCOL)
        )
        self._static_estimate_bytes = static_size * workers * 2
        if self._static_estimate_bytes > memory_budget_bytes:
            raise MemoryError("serialized static catalog/map estimate exceeds memory_budget_bytes.")

        self._context = mp.get_context(start_method)
        self._task_queue = self._context.Queue(maxsize=window)
        self._result_queue = self._context.Queue(maxsize=window + workers + 2)
        self._processes: list[Any] = []
        self._worker_rss: dict[int, int] = {}
        self._startup_s = 0.0
        self._closed = False
        self._active = False
        self._last_timings = CandidatePoolTimings(
            0, 0, 0, 0, 0, 0, 0, 0, (), self._static_estimate_bytes
        )

        started = perf_counter()
        try:
            for worker_id in range(workers):
                process = self._context.Process(
                    target=_worker_main,
                    args=(
                        worker_id,
                        catalog,
                        self._map_payload,
                        self._task_queue,
                        self._result_queue,
                    ),
                    name=f"candidate-score-{worker_id}",
                )
                process.start()
                self._processes.append(process)
            ready: set[int] = set()
            while len(ready) < workers:
                message = self._result_queue.get(timeout=30)
                if message.kind != "ready" or message.worker_id in ready:
                    raise RuntimeError("worker startup returned an invalid ready message.")
                if message.error:
                    raise RuntimeError(
                        f"worker {message.worker_id} failed to initialize: {message.error}"
                    )
                ready.add(message.worker_id)
                self._worker_rss[message.pid] = message.rss_bytes
            self._startup_s = perf_counter() - started
            self._check_live_memory(self._static_estimate_bytes)
        except BaseException:
            self.close()
            raise

    @property
    def last_timings(self) -> CandidatePoolTimings:
        """Return the latest setup, IPC, evaluation, and count measurements."""
        return self._last_timings

    def iter_results(
        self,
        measurements: MeasurementSetV2,
        config: SearchConfig,
        candidates: Iterable[CandidatePositionInput],
    ) -> Iterator[CandidatePositionResult]:
        """Evaluate every record once and yield in stable input order, without deduplication."""
        if not isinstance(measurements, MeasurementSetV2):
            raise TypeError("measurements must be a MeasurementSetV2 instance.")
        if not isinstance(config, SearchConfig):
            raise TypeError("config must be a SearchConfig instance.")
        if self._closed:
            raise RuntimeError("candidate pool is closed.")
        if self._active:
            raise RuntimeError("candidate pool does not support overlapping requests.")
        estimate = self._request_estimate(measurements)
        self._check_live_memory(estimate)
        return self._iter_request(measurements, config, candidates, estimate)

    def score(
        self,
        measurements: MeasurementSetV2,
        config: SearchConfig,
        candidates: Iterable[CandidatePositionInput],
        *,
        search_status: Literal["completed", "budget_exhausted"] = "completed",
        budget_limit: int | None = None,
        budget_used: int | None = None,
        candidate_limit: int | None = None,
        certificate_scope: Literal["none", "finite_candidate_set"] = "none",
    ) -> CandidateSearchRun:
        """Score unique 1e-9-rounded positions with serial accumulator semantics."""
        started = perf_counter()
        if search_status not in ("completed", "budget_exhausted"):
            raise ValueError("unsupported search_status.")
        if certificate_scope not in ("none", "finite_candidate_set"):
            raise ValueError("unsupported certificate_scope.")
        if candidate_limit is not None and (
            type(candidate_limit) is not int or candidate_limit < 1
        ):
            raise ValueError("candidate_limit must be a positive integer.")
        seen_ids: set[tuple[type, str | int]] = set()
        seen_positions: set[tuple[float, float]] = set()
        state = {"raw": 0, "duplicates": 0, "unique": 0, "exhausted": False}

        def unique_candidates() -> Iterator[CandidatePositionInput]:
            for item in candidates:
                state["raw"] += 1
                if not isinstance(item, CandidatePositionInput):
                    raise TypeError("candidates must contain CandidatePositionInput records.")
                id_key = (type(item.candidate_id), item.candidate_id)
                if id_key in seen_ids:
                    raise ValueError("candidate IDs must be unique within a score request.")
                seen_ids.add(id_key)
                position = _position_key(item.position_xy)
                if position in seen_positions:
                    state["duplicates"] += 1
                    continue
                if candidate_limit is not None and state["unique"] >= candidate_limit:
                    state["exhausted"] = True
                    break
                seen_positions.add(position)
                state["unique"] += 1
                yield CandidatePositionInput(item.candidate_id, position)

        hypotheses: list[Hypothesis] = []
        counts: Counter[CandidateStatus] = Counter()
        completed = 0
        try:
            for item in self.iter_results(measurements, config, unique_candidates()):
                completed += 1
                counts[item.evaluation.status] += 1
                if item.evaluation.hypothesis is not None:
                    hypotheses.append(item.evaluation.hypothesis)
        finally:
            self._last_timings = replace(
                self._last_timings,
                input_count=state["raw"],
                evaluation_count=completed,
                duplicate_position_count=state["duplicates"],
            )
        if state["exhausted"]:
            search_status, budget_limit, budget_used = (
                "budget_exhausted",
                candidate_limit,
                state["unique"],
            )
        elif candidate_limit is not None and search_status == "completed" and budget_limit is None:
            budget_limit, budget_used = candidate_limit, state["unique"]
        if search_status == "budget_exhausted" and (
            budget_limit is None or budget_used != budget_limit
        ):
            raise ValueError("budget_exhausted requires budget_used equal to budget_limit.")
        if search_status == "completed" and (budget_limit is None) != (budget_used is None):
            raise ValueError("budget_limit and budget_used must be provided together.")
        ranked = tuple(sorted(hypotheses, key=lambda item: item.score))
        supported = completed > 0 and counts["scored"] + counts["invalid_water"] == completed
        effective_certificate = (
            certificate_scope
            if certificate_scope == "finite_candidate_set"
            and supported
            and search_status == "completed"
            else "none"
        )
        localization = LocalizationResult(
            hypotheses=ranked,
            elapsed_s=perf_counter() - started,
            decision_status="ambiguous" if ranked else "no_fix",
            search_status=search_status,
            certificate_scope=effective_certificate,
            budget_limit=budget_limit,
            budget_used=budget_used,
            report_count=len(measurements.measurements),
            objective_version=config.objective_version,
        )
        return CandidateSearchRun(
            localization, completed, tuple((status, counts[status]) for status in _STATUS_ORDER)
        )

    def _iter_request(
        self,
        measurements: MeasurementSetV2,
        config: SearchConfig,
        candidates: Iterable[CandidatePositionInput],
        memory_estimate: int,
    ) -> Iterator[CandidatePositionResult]:
        if self._active:
            raise RuntimeError("candidate pool does not support overlapping requests.")
        self._active = True
        started = perf_counter()
        request_id = uuid.uuid4().hex
        permits = threading.BoundedSemaphore(self.max_in_flight_batches)
        feeder_done = threading.Event()
        stop_feeder = threading.Event()
        input_count = duplicate_count = submitted = completed = failed = 0
        feeder_errors: list[BaseException] = []
        pending: dict[int, _Message] = {}
        next_batch = 0
        failed_ids: list[str | int] = []
        scorer_setup_s = evaluation_s = ipc_s = 0.0
        error_messages: list[str] = []

        def feed() -> None:
            nonlocal input_count, duplicate_count, submitted, ipc_s
            seen_ids: set[tuple[type, str | int]] = set()
            seen_positions: set[tuple[float, float]] = set()
            sequence = batch_id = 0
            try:
                source = iter(candidates)
                while not stop_feeder.is_set():
                    batch: list[CandidatePositionInput] = []
                    for _ in range(self.batch_size):
                        try:
                            item = next(source)
                        except StopIteration:
                            break
                        input_count += 1
                        if not isinstance(item, CandidatePositionInput):
                            raise TypeError(
                                "candidates must contain CandidatePositionInput records."
                            )
                        id_key = (type(item.candidate_id), item.candidate_id)
                        if id_key in seen_ids:
                            raise ValueError("candidate IDs must be unique within a request.")
                        seen_ids.add(id_key)
                        key = _position_key(item.position_xy)
                        if key in seen_positions:
                            duplicate_count += 1
                        seen_positions.add(key)
                        batch.append(item)
                    if not batch:
                        break
                    while not stop_feeder.is_set() and not permits.acquire(timeout=0.1):
                        pass
                    if stop_feeder.is_set():
                        break
                    task = _Task(request_id, batch_id, sequence, tuple(batch), measurements, config)
                    sequence += len(batch)
                    started_put = perf_counter()
                    enqueued = False
                    while not stop_feeder.is_set():
                        try:
                            self._task_queue.put(task, timeout=0.1)
                            enqueued = True
                            break
                        except queue.Full:
                            continue
                    ipc_s += perf_counter() - started_put
                    if not enqueued:
                        permits.release()
                        break
                    submitted += 1
                    batch_id += 1
            except BaseException as exc:
                feeder_errors.append(exc)
            finally:
                feeder_done.set()

        feeder = threading.Thread(target=feed, name="candidate-pool-feeder", daemon=True)
        feeder.start()
        try:
            while not feeder_done.is_set() or next_batch < submitted:
                started_get = perf_counter()
                try:
                    message = self._result_queue.get(timeout=0.1)
                except queue.Empty:
                    message = None
                ipc_s += perf_counter() - started_get
                if message is None:
                    dead = [
                        process for process in self._processes if process.exitcode not in (None, 0)
                    ]
                    if dead:
                        error_messages.extend(
                            f"{p.name} exited with code {p.exitcode}" for p in dead
                        )
                        stop_feeder.set()
                        break
                    continue
                if message.kind == "stopped":
                    error_messages.append(
                        message.error or f"worker {message.worker_id} stopped unexpectedly"
                    )
                    continue
                if message.kind == "ready":
                    error_messages.append("unexpected worker-ready message during request")
                    continue
                self._worker_rss[message.pid] = message.rss_bytes
                scorer_setup_s += message.scorer_setup_s
                evaluation_s += message.evaluation_s
                pending[message.batch_id] = message
                while next_batch in pending:
                    ready = pending.pop(next_batch)
                    permits.release()
                    next_batch += 1
                    if ready.error:
                        error_messages.append(ready.error)
                    for entry in ready.entries:
                        if entry.result is not None:
                            completed += 1
                            yield entry.result
                        else:
                            failed += 1
                            failed_ids.append(entry.candidate_id)
                            error_messages.append(entry.error or "candidate evaluation failed")
                try:
                    self._check_live_memory(memory_estimate)
                except MemoryError as exc:
                    error_messages.append(str(exc))
                    stop_feeder.set()
                    break
            feeder.join(timeout=2)
            if feeder.is_alive():
                stop_feeder.set()
                feeder.join(timeout=2)
                error_messages.append("candidate source feeder did not stop after worker failure")
            if feeder_errors:
                error_messages.append(_error_text(feeder_errors[0]))
            if pending or next_batch != submitted:
                error_messages.append(
                    "worker output lost, duplicated, or reordered a candidate batch"
                )
            if error_messages:
                outstanding = max(0, input_count - completed - failed)
                raise CandidatePoolError(
                    "parallel candidate evaluation failed; result stream is incomplete.",
                    completed_count=completed,
                    failed_count=failed,
                    outstanding_count=outstanding,
                    failed_candidate_ids=failed_ids,
                ) from RuntimeError("; ".join(error_messages))
        finally:
            stop_feeder.set()
            if feeder.is_alive():
                feeder.join(timeout=2)
            self._active = False
            self._last_timings = CandidatePoolTimings(
                self._startup_s,
                scorer_setup_s,
                ipc_s,
                evaluation_s,
                perf_counter() - started,
                input_count,
                completed,
                duplicate_count,
                tuple(sorted(self._worker_rss.items())),
                memory_estimate,
            )

    def _request_estimate(self, measurements: MeasurementSetV2) -> int:
        reports = max(1, len(measurements.measurements))
        groups = max(1, len(self.catalog.records))
        per_candidate = reports * groups * 64 + 4096
        dynamic = self.max_in_flight_batches * self.batch_size * per_candidate
        return self._static_estimate_bytes + dynamic

    def _check_live_memory(self, estimate: int) -> None:
        if estimate > self.memory_budget_bytes:
            raise MemoryError(
                f"estimated pool memory {estimate} exceeds budget {self.memory_budget_bytes}."
            )
        rss = {process.pid: _rss_bytes(process.pid) for process in self._processes if process.pid}
        self._worker_rss.update(rss)
        measured = sum(value for value in rss.values() if value > 0)
        dynamic_estimate = max(0, estimate - self._static_estimate_bytes)
        if measured + dynamic_estimate > self.memory_budget_bytes:
            raise MemoryError(
                f"measured worker RSS plus in-flight estimate {measured + dynamic_estimate} "
                f"exceeds budget {self.memory_budget_bytes}."
            )

    def close(self) -> None:
        """Stop persistent workers and release their IPC queues."""
        if self._closed:
            return
        self._closed = True
        for _ in self._processes:
            try:
                self._task_queue.put_nowait(None)
            except queue.Full:
                break
        for process in self._processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        self._task_queue.close()
        self._result_queue.close()

    def __enter__(self) -> PersistentCandidatePool:
        if self._closed:
            raise RuntimeError("candidate pool is closed.")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _map_payload(vector_map: VectorMap) -> _MapPayload:
    coverage = getattr(vector_map, "_coverage")
    unknown = getattr(vector_map, "_unknown")
    return _MapPayload(
        vector_map.crs.to_string(),
        vector_map.land_geometry.wkb,
        None if coverage is None else coverage.wkb,
        None if unknown is None else unknown.wkb,
    )


__all__ = [
    "CandidatePositionInput",
    "CandidatePositionResult",
    "CandidatePoolTimings",
    "CandidatePoolError",
    "PersistentCandidatePool",
]
