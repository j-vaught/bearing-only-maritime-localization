"""Persistent bounded CPU workers for finite-candidate chunks."""

from __future__ import annotations

import multiprocessing as mp
import pickle
import queue
import uuid
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from shapely import from_wkb

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime.schemas.localization import Hypothesis

from .config import SearchConfig
from .scoring import CandidateScorer, CandidateStatus
from .screened_search import CandidatePosition, screened_candidate_search

ExecutionMode = Literal["exact", "screened"]
AuditAction = Literal["exact_scored", "exact_invalid_water", "exact_unresolved", "pruned"]


@dataclass(frozen=True, slots=True)
class CandidateAudit:
    candidate_id: str
    input_index: int
    position_xy_m: tuple[float, float]
    action: AuditAction
    score: float | None
    lower_bound: float | None
    lower_bound_supported: bool | None
    visible_group_count_k: int | None
    model_status: CandidateStatus | None


@dataclass(frozen=True, slots=True)
class ScreenedChunkResult:
    chunk_index: int
    candidates: tuple[CandidateAudit, ...]
    hypotheses: tuple[tuple[int, Hypothesis], ...]
    candidate_count: int
    exact_scored_count: int
    exact_evaluation_count: int
    invalid_water_count: int
    unresolved_count: int
    pruned_count: int
    unvisited_count: int
    incumbent_seed_evaluation_count: int
    incumbent_validation_count: int
    screened_elapsed_s: float
    exact_elapsed_s: float
    worker_evaluation_s: float
    estimated_memory_bytes: int
    worker_rss_bytes: tuple[tuple[int, int], ...]
    request_complete: bool
    finite_candidate_set: bool


@dataclass(frozen=True, slots=True)
class ParallelScreenedTimings:
    pool_startup_s: float
    scorer_setup_s: float
    ipc_s: float
    worker_evaluation_s: float
    elapsed_s: float
    input_count: int
    completed_count: int
    failed_count: int
    estimated_memory_bytes: int
    worker_rss_bytes: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class CandidatePoolDisposition:
    """Disjoint per-ID state for one request at failure or cancellation."""

    consumed_candidate_ids: tuple[str, ...]
    submitted_candidate_ids: tuple[str, ...]
    committed_candidate_ids: tuple[str, ...]
    pending_candidate_ids: tuple[str, ...]
    failed_candidate_ids: tuple[str, ...]
    outstanding_candidate_ids: tuple[str, ...]
    not_dispatched_candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        categories = (
            self.committed_candidate_ids,
            self.pending_candidate_ids,
            self.failed_candidate_ids,
            self.outstanding_candidate_ids,
        )
        flattened = [candidate_id for category in categories for candidate_id in category]
        if len(flattened) != len(set(flattened)):
            raise ValueError("submitted candidate dispositions must be pairwise disjoint.")
        outcome_ids = set(flattened)
        if outcome_ids != set(self.submitted_candidate_ids):
            raise ValueError("every submitted candidate must have exactly one disposition.")
        all_categories = flattened + list(self.not_dispatched_candidate_ids)
        if len(all_categories) != len(set(all_categories)):
            raise ValueError("consumed candidate dispositions must be pairwise disjoint.")
        if set(all_categories) != set(self.consumed_candidate_ids):
            raise ValueError("every consumed candidate must have exactly one disposition.")

    @property
    def completed_count(self) -> int:
        """Number of chunk results yielded to the caller."""
        return len(self.committed_candidate_ids)

    @property
    def pending_count(self) -> int:
        return len(self.pending_candidate_ids)

    @property
    def failed_count(self) -> int:
        return len(self.failed_candidate_ids)

    @property
    def outstanding_count(self) -> int:
        return len(self.outstanding_candidate_ids)


class ParallelScreenedError(RuntimeError):
    def __init__(self, message: str, disposition: CandidatePoolDisposition) -> None:
        super().__init__(message)
        self.disposition = disposition
        self.completed_count = disposition.completed_count
        self.pending_count = disposition.pending_count
        self.failed_count = disposition.failed_count
        self.outstanding_count = disposition.outstanding_count
        self.failed_candidate_ids = disposition.failed_candidate_ids
        self.pending_candidate_ids = disposition.pending_candidate_ids
        self.outstanding_candidate_ids = disposition.outstanding_candidate_ids
        self.not_dispatched_candidate_ids = disposition.not_dispatched_candidate_ids


@dataclass(frozen=True, slots=True)
class _Map:
    crs: str
    land: bytes | None
    coverage: bytes | None
    unknown: bytes | None


@dataclass(frozen=True, slots=True)
class _Point:
    cid: str
    index: int
    xy: tuple[float, float]


@dataclass(frozen=True, slots=True)
class _Task:
    request: str
    chunk: int
    points: tuple[_Point, ...]
    measurements: MeasurementSetV2
    config: SearchConfig
    mode: ExecutionMode
    incumbent: _Point | None


@dataclass(frozen=True, slots=True)
class _Chunk:
    index: int
    audits: tuple[CandidateAudit, ...]
    hypotheses: tuple[tuple[int, Hypothesis], ...]
    exact_scored: int
    exact_evaluations: int
    invalid: int
    unresolved: int
    pruned: int
    seeds: int
    validations: int
    screened_s: float
    exact_s: float


@dataclass(frozen=True, slots=True)
class _Msg:
    kind: Literal["ready", "chunk", "stopped"]
    worker: int
    pid: int
    rss: int
    index: int | None = None
    chunk: _Chunk | None = None
    setup_s: float = 0.0
    eval_s: float = 0.0
    error: str | None = None


def _worker(worker: int, catalog: CatalogV2, payload: _Map, tasks: Any, results: Any) -> None:
    try:
        vm = VectorMap(
            from_wkb(payload.land) if payload.land else None,
            crs=payload.crs,
            coverage_geometry=from_wkb(payload.coverage) if payload.coverage else None,
            unknown_geometry=from_wkb(payload.unknown) if payload.unknown else None,
        )
        results.put(_Msg("ready", worker, _pid(), _rss()))
    except BaseException as exc:
        results.put(_Msg("ready", worker, _pid(), _rss(), error=_err(exc)))
        return
    request = None
    scorer = None
    try:
        while True:
            task = tasks.get()
            if task is None:
                break
            setup = 0.0
            try:
                if task.request != request:
                    scorer = None
                    request = None
                    t = perf_counter()
                    scorer = CandidateScorer(task.measurements, catalog, vm, task.config)
                    setup = perf_counter() - t
                    request = task.request
                if scorer is None:
                    raise RuntimeError("worker scorer not initialized")
                t = perf_counter()
                chunk = _evaluate(scorer, task)
                elapsed = perf_counter() - t
                results.put(
                    _Msg("chunk", worker, _pid(), _rss(), task.chunk, chunk, setup, elapsed)
                )
            except BaseException as exc:
                results.put(
                    _Msg(
                        "chunk", worker, _pid(), _rss(), task.chunk, setup_s=setup, error=_err(exc)
                    )
                )
    except BaseException as exc:
        results.put(_Msg("stopped", worker, _pid(), _rss(), error=_err(exc)))
        return
    results.put(_Msg("stopped", worker, _pid(), _rss()))


def _evaluate(scorer: CandidateScorer, task: _Task) -> _Chunk:
    if task.mode == "exact":
        t = perf_counter()
        audits = []
        hyps = []
        counts = {"scored": 0, "invalid_water": 0, "unresolved_coverage": 0, "unresolved_model": 0}
        for p in task.points:
            e = scorer.evaluate(p.xy)
            counts[e.status] += 1
            if e.status == "scored" and e.hypothesis is not None:
                action = "exact_scored"
                score = e.hypothesis.score
                hyps.append((p.index, e.hypothesis))
            elif e.status == "invalid_water":
                action = "exact_invalid_water"
                score = None
            else:
                action = "exact_unresolved"
                score = None
            audits.append(
                CandidateAudit(p.cid, p.index, p.xy, action, score, None, None, None, e.status)
            )
        elapsed = perf_counter() - t
        return _Chunk(
            task.chunk,
            tuple(audits),
            tuple(hyps),
            counts["scored"],
            len(audits),
            counts["invalid_water"],
            counts["unresolved_coverage"] + counts["unresolved_model"],
            0,
            0,
            0,
            0.0,
            elapsed,
        )
    sentinel = f"__parallel_seed__:{task.request}:{task.chunk}"
    if task.incumbent is None:
        points = tuple(CandidatePosition(p.cid, p.xy) for p in task.points)
    else:
        points = (CandidatePosition(sentinel, task.incumbent.xy),) + tuple(
            CandidatePosition(p.cid, p.xy) for p in task.points
        )
    t = perf_counter()
    result = screened_candidate_search(
        scorer, points, visit_budget=len(points), complete_candidate_count=len(points)
    )
    elapsed = perf_counter() - t
    byid = {p.cid: p for p in task.points}
    audits = []
    hyps = []
    counts = {"scored": 0, "invalid_water": 0, "unresolved_coverage": 0, "unresolved_model": 0}
    pruned = 0
    for v in result.visits:
        if task.incumbent is not None and v.candidate_id == sentinel:
            continue
        p = byid[v.candidate_id]
        if v.action == "pruned":
            pruned += 1
            audits.append(
                CandidateAudit(
                    p.cid,
                    p.index,
                    p.xy,
                    "pruned",
                    None,
                    None if v.lower_bound is None else v.lower_bound.bound,
                    None if v.lower_bound is None else v.lower_bound.supported,
                    None if v.visibility is None else v.visibility.minimum_visible_group_count,
                    None,
                )
            )
            continue
        status = v.evaluation_status
        if status is None:
            raise RuntimeError("exact visit missing model status")
        counts[status] += 1
        action = (
            "exact_scored"
            if v.action == "exact_scored"
            else "exact_invalid_water"
            if v.action == "exact_invalid_water"
            else "exact_unresolved"
        )
        audits.append(
            CandidateAudit(
                p.cid,
                p.index,
                p.xy,
                action,
                v.score,
                None if v.lower_bound is None else v.lower_bound.bound,
                None if v.lower_bound is None else v.lower_bound.supported,
                None if v.visibility is None else v.visibility.minimum_visible_group_count,
                status,
            )
        )
        if v.action == "exact_scored":
            h = next((h for h in result.hypotheses if (h.east_m, h.north_m) == p.xy), None)
            if h is None:
                raise RuntimeError("exact candidate has no hypothesis")
            hyps.append((p.index, h))
    audits.sort(key=lambda x: x.input_index)
    hyps.sort(key=lambda x: x[0])
    return _Chunk(
        task.chunk,
        tuple(audits),
        tuple(hyps),
        counts["scored"],
        sum(counts.values()),
        counts["invalid_water"],
        counts["unresolved_coverage"] + counts["unresolved_model"],
        pruned,
        int(task.incumbent is not None),
        result.incumbent_validation_count,
        elapsed,
        result.exact_score_elapsed_s,
    )


class ParallelScreenedCandidatePool:
    """Persistent bounded pool. Chunk results stream in stable order for checkpointing."""

    def __init__(
        self,
        catalog: CatalogV2,
        vector_map: VectorMap,
        *,
        workers: int,
        memory_budget_bytes: int,
        chunk_size: int = 32,
        max_in_flight_chunks: int | None = None,
        startup_timeout_s: float = 30.0,
        request_timeout_s: float | None = None,
    ) -> None:
        if not isinstance(catalog, CatalogV2) or not isinstance(vector_map, VectorMap):
            raise TypeError("catalog and vector_map must use their contract types")
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be positive")
        if type(chunk_size) is not int or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        window = 2 * workers if max_in_flight_chunks is None else max_in_flight_chunks
        if type(window) is not int or window < workers:
            raise ValueError("in-flight window must be >= workers")
        if type(memory_budget_bytes) is not int or memory_budget_bytes < 1:
            raise ValueError("memory budget must be positive")
        if not isfinite(startup_timeout_s) or startup_timeout_s <= 0:
            raise ValueError("invalid startup timeout")
        if request_timeout_s is not None and (
            not isfinite(request_timeout_s) or request_timeout_s <= 0
        ):
            raise ValueError("invalid request timeout")
        self.catalog = catalog
        self.workers = workers
        self.chunk_size = chunk_size
        self.window = window
        self.memory_budget_bytes = memory_budget_bytes
        self.request_timeout_s = request_timeout_s
        land = vector_map.land_geometry
        self._map = _Map(
            vector_map.crs.to_string(),
            None if land.is_empty else land.wkb,
            None if vector_map._coverage is None else vector_map._coverage.wkb,
            None if vector_map._unknown is None else vector_map._unknown.wkb,
        )
        size = len(pickle.dumps((catalog, self._map), protocol=pickle.HIGHEST_PROTOCOL))
        self._static = size * workers * 2
        if self._static > memory_budget_bytes:
            raise MemoryError("static serialization estimate exceeds memory budget")
        self._ctx = mp.get_context("spawn")
        self._tasks = self._ctx.Queue(maxsize=window)
        self._results = self._ctx.Queue(maxsize=window + workers + 2)
        self._procs = []
        self._rssmap = {}
        self._startup = 0.0
        self._closed = self._failed = self._active = False
        self._timings = ParallelScreenedTimings(0, 0, 0, 0, 0, 0, 0, 0, 0, ())
        self._last_disposition = CandidatePoolDisposition((), (), (), (), (), (), ())
        start = perf_counter()
        try:
            for wid in range(workers):
                p = self._ctx.Process(
                    target=_worker,
                    args=(wid, catalog, self._map, self._tasks, self._results),
                    name=f"screened-cpu-{wid}",
                )
                p.start()
                self._procs.append(p)
            ready = set()
            while len(ready) < workers:
                if perf_counter() - start > startup_timeout_s:
                    raise TimeoutError("worker startup timed out")
                try:
                    msg = self._results.get(timeout=0.1)
                except queue.Empty:
                    _dead(self._procs, "during startup")
                    continue
                if msg.kind != "ready" or msg.worker in ready:
                    raise RuntimeError("invalid ready message")
                if msg.error:
                    raise RuntimeError(f"worker startup failed: {msg.error}")
                ready.add(msg.worker)
                self._rssmap[msg.pid] = msg.rss
            self._startup = perf_counter() - start
            self._memory(self._static)
        except BaseException:
            self._abort()
            raise

    @property
    def last_timings(self) -> ParallelScreenedTimings:
        return self._timings

    @property
    def last_disposition(self) -> CandidatePoolDisposition:
        """Return the last request's committed, pending, failed, and outstanding IDs."""
        return self._last_disposition

    def iter_chunks(
        self,
        measurements: MeasurementSetV2,
        config: SearchConfig,
        candidates: Iterable[CandidatePosition],
        *,
        mode: ExecutionMode = "screened",
        complete_candidate_count: int | None = None,
    ) -> Generator[ScreenedChunkResult, None, None]:
        if not isinstance(measurements, MeasurementSetV2) or not isinstance(config, SearchConfig):
            raise TypeError("measurements and config must use their contract types")
        if mode not in ("exact", "screened"):
            raise ValueError("mode must be exact or screened")
        if complete_candidate_count is not None and (
            type(complete_candidate_count) is not int or complete_candidate_count < 0
        ):
            raise ValueError("complete count must be nonnegative or None")
        if self._closed or self._failed:
            raise RuntimeError("pool is closed or failed")
        if self._active:
            raise RuntimeError("overlapping requests are unsupported")
        estimate = self._estimate(measurements)
        self._memory(estimate)
        return self._request(
            measurements, config, candidates, mode, complete_candidate_count, estimate
        )

    def _request(
        self,
        measurements: MeasurementSetV2,
        config: SearchConfig,
        candidates: Iterable[CandidatePosition],
        mode: ExecutionMode,
        complete: int | None,
        estimate: int,
    ) -> Generator[ScreenedChunkResult, None, None]:
        self._active = True
        start = perf_counter()
        deadline = None if self.request_timeout_s is None else start + self.request_timeout_s
        request = uuid.uuid4().hex
        source = iter(candidates)
        seen = set()
        consumed_ids: list[str] = []
        submitted_ids: list[str] = []
        committed_ids: list[str] = []
        failed_ids: set[str] = set()
        input_n = dispatch = yielded = completed = failed = unresolved = 0
        sizes = {}
        ids = {}
        pending = {}
        incumbent = None
        incumbent_key = None
        eof = False
        setup = worker_s = ipc = 0.0
        success = False
        try:
            while not eof or yielded < dispatch:
                while not eof and dispatch - yielded < self.window:
                    points = []
                    for _ in range(self.chunk_size):
                        try:
                            item = next(source)
                        except StopIteration:
                            eof = True
                            break
                        if not isinstance(item, CandidatePosition):
                            raise TypeError("expected CandidatePosition")
                        if item.candidate_id in seen:
                            raise ValueError(f"duplicate candidate ID {item.candidate_id!r}")
                        seen.add(item.candidate_id)
                        consumed_ids.append(item.candidate_id)
                        points.append(
                            _Point(
                                item.candidate_id,
                                input_n,
                                (float(item.position_xy_m[0]), float(item.position_xy_m[1])),
                            )
                        )
                        input_n += 1
                    if not points:
                        break
                    if dispatch >= 1_000_000_000:
                        raise ValueError("candidate index limit exceeded")
                    task = _Task(
                        request, dispatch, tuple(points), measurements, config, mode, incumbent
                    )
                    sizes[dispatch] = len(points)
                    ids[dispatch] = tuple(p.cid for p in points)
                    while True:
                        try:
                            self._tasks.put(task, timeout=0.1)
                            break
                        except queue.Full:
                            if deadline and perf_counter() >= deadline:
                                raise TimeoutError("request timed out")
                            _dead(self._procs, "while dispatching")
                    submitted_ids.extend(ids[dispatch])
                    dispatch += 1
                if eof and complete is not None and input_n != complete:
                    raise ValueError(f"candidate count {input_n} != declared {complete}")
                if yielded >= dispatch:
                    continue
                if deadline and perf_counter() >= deadline:
                    raise TimeoutError("request timed out")
                t = perf_counter()
                try:
                    msg = self._results.get(timeout=0.1)
                except queue.Empty:
                    msg = None
                ipc += perf_counter() - t
                if msg is None:
                    _dead(self._procs, "during request")
                    self._memory(estimate)
                    continue
                if msg.kind == "stopped":
                    raise RuntimeError(msg.error or f"worker {msg.worker} stopped")
                if msg.kind == "ready":
                    raise RuntimeError("unexpected ready message")
                ci = msg.chunk.index if msg.chunk is not None else msg.index
                if ci is None or ci not in sizes:
                    raise RuntimeError("failed or unsubmitted chunk")
                if ci < yielded or ci in pending:
                    raise RuntimeError(f"duplicate chunk {ci}")
                if msg.error is None:
                    if msg.chunk is None or len(msg.chunk.audits) != sizes[ci]:
                        failed_ids.update(ids[ci])
                        raise RuntimeError(f"incomplete chunk {ci}")
                else:
                    failed += sizes[ci]
                    failed_ids.update(ids[ci])
                self._rssmap[msg.pid] = msg.rss
                setup += msg.setup_s
                worker_s += msg.eval_s
                pending[ci] = msg
                while yielded in pending:
                    ready = pending.pop(yielded)
                    if ready.error:
                        raise RuntimeError(ready.error)
                    chunk = ready.chunk
                    if chunk is None or tuple(a.candidate_id for a in chunk.audits) != ids[yielded]:
                        failed_ids.update(ids[yielded])
                        raise RuntimeError("chunk candidate IDs mismatch")
                    completed += len(chunk.audits)
                    unresolved += chunk.unresolved
                    for index, h in chunk.hypotheses:
                        key = (h.score, h.east_m, h.north_m, index)
                        if incumbent_key is None or key < incumbent_key:
                            audit = next(a for a in chunk.audits if a.input_index == index)
                            incumbent = _Point(audit.candidate_id, index, audit.position_xy_m)
                            incumbent_key = key
                    final = eof and yielded + 1 == dispatch
                    finite = final and unresolved == 0 and completed == input_n
                    self._timings = ParallelScreenedTimings(
                        self._startup,
                        setup,
                        ipc,
                        worker_s,
                        perf_counter() - start,
                        input_n,
                        completed,
                        failed,
                        estimate,
                        tuple(sorted(self._rssmap.items())),
                    )
                    committed_ids.extend(audit.candidate_id for audit in chunk.audits)
                    yielded += 1
                    self._last_disposition = _make_disposition(
                        submitted_ids, committed_ids, pending, ids, failed_ids, consumed_ids
                    )
                    yield ScreenedChunkResult(
                        chunk.index,
                        chunk.audits,
                        chunk.hypotheses,
                        len(chunk.audits),
                        chunk.exact_scored,
                        chunk.exact_evaluations,
                        chunk.invalid,
                        chunk.unresolved,
                        chunk.pruned,
                        0,
                        chunk.seeds,
                        chunk.validations,
                        chunk.screened_s,
                        chunk.exact_s,
                        worker_s,
                        estimate,
                        tuple(sorted(self._rssmap.items())),
                        final,
                        finite,
                    )
                    self._memory(estimate)
            success = eof and completed == input_n and failed == 0 and not pending
            self._last_disposition = _make_disposition(
                submitted_ids, committed_ids, pending, ids, failed_ids, consumed_ids
            )
            self._active = False
        except BaseException as exc:
            disposition = _make_disposition(
                submitted_ids, committed_ids, pending, ids, failed_ids, consumed_ids
            )
            self._last_disposition = disposition
            self._timings = ParallelScreenedTimings(
                self._startup,
                setup,
                ipc,
                worker_s,
                perf_counter() - start,
                input_n,
                disposition.completed_count,
                disposition.failed_count,
                estimate,
                tuple(sorted(self._rssmap.items())),
            )
            if isinstance(exc, (GeneratorExit, KeyboardInterrupt, SystemExit)):
                raise
            raise ParallelScreenedError(
                "parallel request failed; partial output is incomplete", disposition
            ) from exc
        finally:
            if not success:
                self._abort()

    def _estimate(self, m: MeasurementSetV2) -> int:
        per = max(1, len(m.measurements)) * max(1, len(self.catalog.records)) * 64 + 4096
        return self._static + self.window * self.chunk_size * per

    def _memory(self, estimate: int) -> None:
        if estimate > self.memory_budget_bytes:
            raise MemoryError(f"estimate {estimate} exceeds budget")
        live = {p.pid: _rss(p.pid) for p in self._procs if p.pid is not None}
        self._rssmap.update(live)
        measured = sum(v for v in live.values() if v > 0)
        if measured + max(0, estimate - self._static) > self.memory_budget_bytes:
            raise MemoryError("measured worker RSS plus in-flight estimate exceeds budget")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for _ in self._procs:
            try:
                self._tasks.put_nowait(None)
            except queue.Full:
                break
        for p in self._procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
                p.join(timeout=2)
        self._tasks.close()
        self._results.close()

    def _abort(self) -> None:
        self._closed = self._failed = True
        self._active = False
        for p in self._procs:
            if p.is_alive():
                p.terminate()
            p.join(timeout=2)
            if p.is_alive():
                p.kill()
                p.join(timeout=2)
        for q in (self._tasks, self._results):
            try:
                q.cancel_join_thread()
                q.close()
            except (AttributeError, OSError, ValueError):
                pass

    def __enter__(self) -> ParallelScreenedCandidatePool:
        if self._closed:
            raise RuntimeError("pool is closed")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def _make_disposition(
    submitted_ids: list[str],
    committed_ids: list[str],
    pending: dict[int, _Msg],
    chunk_ids: dict[int, tuple[str, ...]],
    failed_ids: set[str],
    consumed_ids: list[str],
) -> CandidatePoolDisposition:
    """Partition every consumed ID without promoting buffered output to committed."""
    submitted = set(submitted_ids)
    committed = set(committed_ids)
    pending_ids = {
        candidate_id
        for chunk_index, message in pending.items()
        if message.error is None and message.chunk is not None
        for candidate_id in chunk_ids[chunk_index]
    }
    failed = failed_ids & submitted
    pending_ids &= submitted
    pending_ids -= committed | failed
    outstanding = submitted - committed - failed - pending_ids
    not_dispatched = set(consumed_ids) - submitted
    order = {candidate_id: index for index, candidate_id in enumerate(consumed_ids)}

    def sort_ids(values: set[str]) -> tuple[str, ...]:
        return tuple(sorted(values, key=order.__getitem__))

    return CandidatePoolDisposition(
        consumed_candidate_ids=tuple(consumed_ids),
        submitted_candidate_ids=sort_ids(submitted),
        committed_candidate_ids=sort_ids(committed),
        pending_candidate_ids=sort_ids(pending_ids),
        failed_candidate_ids=sort_ids(failed),
        outstanding_candidate_ids=sort_ids(outstanding),
        not_dispatched_candidate_ids=sort_ids(not_dispatched),
    )


def _dead(procs: list[Any], phase: str) -> None:
    dead = [p for p in procs if p.exitcode is not None]
    if dead:
        raise RuntimeError(", ".join(f"{p.name} exited {p.exitcode}" for p in dead) + f" {phase}")


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _pid() -> int:
    import os

    return os.getpid()


def _rss(pid: int | None = None) -> int:
    import os

    try:
        return int(Path(f"/proc/{pid or os.getpid()}/statm").read_text().split()[1]) * int(
            os.sysconf("SC_PAGE_SIZE")
        )
    except (OSError, IndexError, ValueError):
        return 0


__all__ = [
    "CandidateAudit",
    "CandidatePoolDisposition",
    "ParallelScreenedCandidatePool",
    "ParallelScreenedError",
    "ParallelScreenedTimings",
    "ScreenedChunkResult",
]
