"""Concurrent, deterministic execution of CPU-prepared GPU search work."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from importlib import import_module
from queue import Queue
from threading import BoundedSemaphore, Thread
from typing import Any, Literal

from ..config import SearchConfig
from ..telemetry import SearchTelemetry
from .costs import (
    CandidateBearingInput,
    CandidateLikelihoodCosts,
    iter_candidate_likelihood_costs_cupy,
)


@dataclass(frozen=True, slots=True)
class _Chunk:
    index: int
    candidates: tuple[CandidateBearingInput, ...]


@dataclass(frozen=True, slots=True)
class _Outcome:
    index: int
    candidate_ids: tuple[str | int, ...]
    results: tuple[CandidateLikelihoodCosts, ...] = ()
    error: BaseException | None = None
    worker_done: bool = False
    feeder_done: bool = False


def iter_candidate_likelihood_costs_multi_gpu(
    measurements: Any,
    candidates: Iterable[CandidateBearingInput],
    config: SearchConfig,
    *,
    device_ids: Sequence[int],
    batch_size: int = 8,
    max_in_flight_chunks: int | None = None,
    max_pairs_per_chunk: int = 65_536,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | None = None,
    telemetry: SearchTelemetry | None = None,
) -> Iterator[CandidateLikelihoodCosts]:
    """Score sorted, CPU-prepared candidates across devices with bounded queues.

    Candidate IDs must be strictly increasing (integers before strings) so the
    initial static chunks and the final CPU merge have a stable identity order.
    Each device receives one initial static chunk. Remaining chunks use a shared
    queue, allowing idle devices to help with uneven workloads. Each device gets
    a read-only view of caller-owned candidate inputs; no cross-device memory
    pooling is assumed. All candidates are rerun with the A27 CPU FP64 scorer
    before they are yielded, so no GPU result selects or accepts a fix.

    The bounded in-flight window limits retained result matrices to at most
    ``max_in_flight_chunks * batch_size`` candidates, in addition to the source
    iterator and one feeder chunk. If a worker fails, the error is raised after
    pending work is drained; results yielded before that point are partial.
    """
    if not isinstance(config, SearchConfig):
        raise TypeError("config must be SearchConfig.")
    devices = tuple(device_ids)
    if not devices or any(type(device) is not int or device < 0 for device in devices):
        raise ValueError("device_ids must contain one or more nonnegative integer IDs.")
    if len(set(devices)) != len(devices):
        raise ValueError("device_ids must be unique.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer.")
    window = max_in_flight_chunks if max_in_flight_chunks is not None else 2 * len(devices)
    if type(window) is not int or window < len(devices):
        raise ValueError("max_in_flight_chunks must be at least the number of devices.")
    if type(max_pairs_per_chunk) is not int or max_pairs_per_chunk < 1:
        raise ValueError("max_pairs_per_chunk must be a positive integer.")
    if telemetry is not None and not isinstance(telemetry, SearchTelemetry):
        raise TypeError("telemetry must be SearchTelemetry or None.")

    shared: Queue[_Chunk | None] = Queue(maxsize=window)
    outputs: Queue[_Outcome] = Queue(maxsize=window + len(devices) + 1)
    static_queues: list[Queue[_Chunk | None]] = [Queue(maxsize=1) for _ in devices]
    permits = BoundedSemaphore(window)

    def score_chunk(device_id: int, chunk: _Chunk) -> _Outcome:
        ids = tuple(item.candidate_id for item in chunk.candidates)
        try:
            cp = import_module("cupy")
            with cp.cuda.Device(device_id):
                values = tuple(
                    iter_candidate_likelihood_costs_cupy(
                        measurements,
                        chunk.candidates,
                        config,
                        batch_size=len(chunk.candidates),
                        max_pairs_per_chunk=max_pairs_per_chunk,
                        observation_types=observation_types,
                        observation_sigmas_deg=observation_sigmas_deg,
                        telemetry=telemetry,
                        cpu_verify_all=True,
                    )
                )
            actual = tuple(item.candidate_id for item in values)
            if actual != ids or any(not item.cpu_verified for item in values):
                raise RuntimeError("A27 scorer lost, duplicated, or left a candidate unverified.")
            return _Outcome(chunk.index, ids, values)
        except BaseException as exc:
            if telemetry is not None:
                telemetry.record_exception(exc, stage="multi_gpu_worker")
            return _Outcome(chunk.index, ids, error=exc)

    def worker(index: int, device_id: int) -> None:
        try:
            initial = static_queues[index].get()
            if initial is not None:
                outputs.put(score_chunk(device_id, initial))
            while True:
                chunk = shared.get()
                if chunk is None:
                    break
                outputs.put(score_chunk(device_id, chunk))
        except BaseException as exc:
            if telemetry is not None:
                telemetry.record_exception(exc, stage="multi_gpu_worker_loop")
            outputs.put(_Outcome(-1, (), error=exc))
        finally:
            outputs.put(_Outcome(-1, (), worker_done=True))

    threads = [
        Thread(target=worker, args=(index, device), name=f"gpu-search-{device}", daemon=True)
        for index, device in enumerate(devices)
    ]
    for thread in threads:
        thread.start()

    feeder_error: list[BaseException] = []
    chunk_count = 0

    def feed() -> None:
        nonlocal chunk_count
        iterator = iter(candidates)
        previous_key: tuple[int, int, str] | None = None

        def read_chunk(index: int) -> _Chunk | None:
            nonlocal previous_key
            values: list[CandidateBearingInput] = []
            for _ in range(batch_size):
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                if not isinstance(item, CandidateBearingInput):
                    raise TypeError("candidates must contain CandidateBearingInput records.")
                key = _candidate_key(item.candidate_id)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("candidate IDs must be unique and strictly increasing.")
                previous_key = key
                values.append(item)
            return _Chunk(index, tuple(values)) if values else None

        try:
            # Seed one fixed chunk per device before opening the shared queue.
            for index, static_queue in enumerate(static_queues):
                chunk = read_chunk(index)
                if chunk is None:
                    static_queue.put(None)
                    continue
                permits.acquire()
                static_queue.put(chunk)
                chunk_count += 1
            next_index = len(devices)
            while True:
                chunk = read_chunk(next_index)
                if chunk is None:
                    break
                permits.acquire()
                shared.put(chunk)
                chunk_count += 1
                next_index += 1
        except BaseException as exc:
            feeder_error.append(exc)
            if telemetry is not None:
                telemetry.record_exception(exc, stage="multi_gpu_feeder")
        finally:
            for static_queue in static_queues:
                if static_queue.empty():
                    static_queue.put(None)
            for _ in devices:
                shared.put(None)
            outputs.put(_Outcome(-1, (), feeder_done=True))

    feeder = Thread(target=feed, name="gpu-search-feeder", daemon=True)
    feeder.start()
    pending: dict[int, _Outcome] = {}
    next_output = 0
    workers_done = 0
    feeder_done = False
    errors: list[BaseException] = []
    try:
        while workers_done < len(devices) or not feeder_done:
            outcome = outputs.get()
            if outcome.worker_done:
                workers_done += 1
                continue
            if outcome.feeder_done:
                feeder_done = True
                continue
            if outcome.error is not None and outcome.index < 0:
                errors.append(outcome.error)
                continue
            pending[outcome.index] = outcome
            while next_output in pending:
                ready = pending.pop(next_output)
                permits.release()
                next_output += 1
                if ready.error is not None:
                    errors.append(ready.error)
                else:
                    yield from ready.results
        feeder.join()
        for thread in threads:
            thread.join()
        if feeder_error:
            raise feeder_error[0]
        if pending or next_output != chunk_count:
            raise RuntimeError("Multi-GPU runtime completed with missing or duplicate chunks.")
        if errors:
            raise RuntimeError(
                "Multi-GPU candidate scoring failed; no complete result set exists."
            ) from errors[0]
    finally:
        # Drain bounded queues if a caller closes the result iterator early.
        while workers_done < len(devices) or not feeder_done:
            outcome = outputs.get()
            if outcome.worker_done:
                workers_done += 1
            elif outcome.feeder_done:
                feeder_done = True
            elif outcome.index < 0:
                if outcome.error is not None:
                    errors.append(outcome.error)
            else:
                pending[outcome.index] = outcome
            while next_output in pending:
                ready = pending.pop(next_output)
                permits.release()
                if ready.error is not None:
                    errors.append(ready.error)
                next_output += 1
        feeder.join()
        for thread in threads:
            thread.join()


def run_proposal_api_on_device(
    proposal_kind: Literal["pairwise", "wedge", "geographic_pairwise"],
    device_id: int,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one A28 proposal call on an explicit device without implying scaling.

    Geographic pairwise proposals remain explicitly unsupported on GPU. The
    caller must use the A19 CPU geodesic implementation for geographic inputs.
    """
    if type(device_id) is not int or device_id < 0:
        raise ValueError("device_id must be a nonnegative integer.")
    if proposal_kind not in ("pairwise", "wedge", "geographic_pairwise"):
        raise ValueError("proposal_kind must be pairwise, wedge, or geographic_pairwise.")
    from . import proposals

    names = {
        "pairwise": "generate_pairwise_proposals_cupy",
        "wedge": "generate_reverse_wedge_proposals_cupy",
        "geographic_pairwise": "generate_geographic_pairwise_proposals_cupy",
    }
    function = getattr(proposals, names[proposal_kind])
    cp = import_module("cupy")
    with cp.cuda.Device(device_id):
        result = function(*args, **kwargs)
        if proposal_kind == "pairwise":
            # The A28 API is lazy. Keep the requested device current while it runs.
            def on_device() -> Iterator[Any]:
                with cp.cuda.Device(device_id):
                    yield from result

            return on_device()
        return result


def _candidate_key(candidate_id: str | int) -> tuple[int, int, str]:
    if type(candidate_id) is int:
        return (0, candidate_id, "")
    if isinstance(candidate_id, str) and candidate_id:
        return (1, 0, candidate_id)
    raise ValueError("candidate_id must be a nonempty string or integer.")


__all__ = ["iter_candidate_likelihood_costs_multi_gpu", "run_proposal_api_on_device"]
