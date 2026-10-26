"""Truth-free timing and resource telemetry for search programs.

The telemetry object records operational counters and elapsed times only. It
never accepts measurements, candidate coordinates, observer positions, or
simulation truth, and exception messages are deliberately omitted.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import re
import resource
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal

Backend = Literal["cpu", "gpu"]
TransferDirection = Literal["host_to_device", "device_to_host", "device_to_device"]
_LABEL = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_OBJECTIVES = {"bearing_set_map_v1", "legacy_corrected_v0"}
_DIRECTIONS = {"host_to_device", "device_to_host", "device_to_device"}


def _label(value: str, field: str) -> str:
    if not isinstance(value, str) or _LABEL.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase identifier of at most 64 characters.")
    if {"observer", "truth"} & set(value.split("_")):
        raise ValueError(f"{field} cannot contain observer or truth data labels.")
    return value


def _device_id(value: int | str | None) -> int | str | None:
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        return _label(value, "device_id")
    raise TypeError("device_id must be a nonnegative integer, lowercase identifier, or None.")


def _nonnegative_int(value: int, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer.")
    return value


def _elapsed(start: float, end: float) -> float:
    value = end - start
    if value < 0.0:
        raise ValueError("Telemetry clocks must be monotonic.")
    return value


@dataclass(slots=True)
class _StageRecord:
    name: str
    parent: str | None
    wall_s: float = 0.0
    gpu_s: float | None = None
    device_id: int | str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parent": self.parent,
            "wall_s": self.wall_s,
            "gpu_s": self.gpu_s,
            "device_id": self.device_id,
        }


class _GpuEventTimer:
    """Lazy CuPy event timer; importing CuPy is avoided for CPU-only searches."""

    def __init__(self, device_id: int | str | None, synchronize: bool) -> None:
        self.device_id = device_id
        self.synchronize = synchronize
        self.cupy: Any = None
        self.device_context: Any = None
        self.start_event: Any = None
        self.end_event: Any = None

    def __enter__(self) -> _GpuEventTimer:
        try:
            self.cupy = importlib.import_module("cupy")
        except ImportError as exc:
            raise RuntimeError("GPU timing requires the optional CuPy dependency.") from exc
        if self.device_id is not None:
            if not isinstance(self.device_id, int):
                raise TypeError("CuPy event timing requires an integer device_id.")
            self.device_context = self.cupy.cuda.Device(self.device_id)
            self.device_context.__enter__()
        if self.synchronize:
            self.cupy.cuda.get_current_stream().synchronize()
        self.start_event = self.cupy.cuda.Event()
        self.end_event = self.cupy.cuda.Event()
        self.start_event.record()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.end_event.record()
            if self.synchronize:
                self.end_event.synchronize()
        finally:
            if self.device_context is not None:
                self.device_context.__exit__(exc_type, exc, traceback)

    def elapsed_s(self) -> float:
        if self.start_event is None or self.end_event is None:
            return 0.0
        return float(self.cupy.cuda.get_elapsed_time(self.start_event, self.end_event)) / 1000.0


class _StageContext(AbstractContextManager[None]):
    def __init__(
        self, telemetry: SearchTelemetry, name: str, gpu: bool, device_id: int | str | None
    ) -> None:
        self.telemetry = telemetry
        self.name = name
        self.gpu = gpu
        self.device_id = device_id
        self.start: float | None = None
        self.gpu_start: float | None = None
        self.gpu_timer: _GpuEventTimer | None = None
        self.stack_token: contextvars.Token[tuple[str, ...]] | None = None

    def __enter__(self) -> None:
        stack = self.telemetry._stage_stack.get()
        self.stack_token = self.telemetry._stage_stack.set((*stack, self.name))
        try:
            self.start = self.telemetry._clock()
            if self.gpu:
                if self.telemetry._gpu_clock is not None:
                    self.gpu_start = self.telemetry._gpu_clock()
                else:
                    self.gpu_timer = _GpuEventTimer(self.device_id, self.telemetry._synchronize_gpu)
                    self.gpu_timer.__enter__()
            return None
        except BaseException:
            self.telemetry._stage_stack.reset(self.stack_token)
            self.stack_token = None
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            gpu_s = None
            if self.gpu:
                if self.gpu_timer is not None:
                    self.gpu_timer.__exit__(exc_type, exc, traceback)
                    gpu_s = self.gpu_timer.elapsed_s()
                elif self.gpu_start is not None:
                    assert self.telemetry._gpu_clock is not None
                    gpu_s = _elapsed(self.gpu_start, self.telemetry._gpu_clock())
            assert self.start is not None
            wall_s = _elapsed(self.start, self.telemetry._clock())
            stack = self.telemetry._stage_stack.get()
            parent = stack[-2] if len(stack) > 1 else None
            self.telemetry._append_stage(
                _StageRecord(self.name, parent, wall_s, gpu_s, self.device_id)
            )
            if exc is not None:
                self.telemetry.record_exception(exc, stage=self.name)
        finally:
            if self.stack_token is not None:
                self.telemetry._stage_stack.reset(self.stack_token)
        return False


class SearchTelemetry:
    """Collect serializable search telemetry without access to search inputs.

    Args:
        objective_version: Explicit objective identifier used by the search.
        backend: Execution backend label.
        device_ids: Hardware identifiers used by the search. IDs are labels only.
        clock: Monotonic wall clock, injectable for deterministic tests.
        gpu_clock: Optional injectable monotonic GPU clock for tests. When omitted,
            GPU stages use synchronized CuPy CUDA events lazily.
        synchronize_gpu: Synchronize before and after each CUDA timed stage.

    The object is intentionally given no measurements, truth, observer location,
    or result payload. Its fixed-schema report therefore cannot serialize them.
    """

    def __init__(
        self,
        objective_version: str = "bearing_set_map_v1",
        backend: Backend = "cpu",
        device_ids: tuple[int | str, ...] | list[int | str] = (),
        clock: Callable[[], float] = time.perf_counter,
        gpu_clock: Callable[[], float] | None = None,
        synchronize_gpu: bool = True,
    ) -> None:
        if objective_version not in _OBJECTIVES:
            raise ValueError(f"objective_version must be one of {sorted(_OBJECTIVES)}.")
        if backend not in ("cpu", "gpu"):
            raise ValueError("backend must be cpu or gpu.")
        if not callable(clock) or (gpu_clock is not None and not callable(gpu_clock)):
            raise TypeError("clock and gpu_clock must be callable.")
        if type(synchronize_gpu) is not bool:
            raise TypeError("synchronize_gpu must be bool.")
        normalized_devices = tuple(_device_id(item) for item in device_ids)
        if any(item is None for item in normalized_devices):
            raise ValueError("device_ids cannot contain None.")
        self.objective_version = objective_version
        self.backend = backend
        self._device_ids = list(dict.fromkeys(normalized_devices))
        self._clock = clock
        self._gpu_clock = gpu_clock
        self._synchronize_gpu = synchronize_gpu
        self._started = clock()
        self._stages: list[_StageRecord] = []
        self._counts: Counter[str] = Counter()
        self._cap_hits: Counter[str] = Counter()
        self._unresolved: Counter[str] = Counter()
        self._transfers: Counter[tuple[str, int | str | None]] = Counter()
        self._memory: dict[str, int | None] = {"cpu_bytes": None, "gpu_bytes": None}
        self._exceptions: list[dict[str, str | None]] = []
        self._stage_stack: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
            f"search_telemetry_stage_{id(self)}", default=()
        )
        self._lock = threading.RLock()

    def stage(
        self, name: str, *, gpu: bool = False, device_id: int | str | None = None
    ) -> _StageContext:
        """Time a search stage, optionally recording synchronized GPU elapsed time."""
        name = _label(name, "stage name")
        if type(gpu) is not bool:
            raise TypeError("gpu must be bool.")
        normalized_device = _device_id(device_id)
        if gpu and normalized_device is not None:
            with self._lock:
                if normalized_device not in self._device_ids:
                    self._device_ids.append(normalized_device)
        return _StageContext(self, name, gpu, normalized_device)

    def _append_stage(self, stage: _StageRecord) -> None:
        with self._lock:
            self._stages.append(stage)

    def record_candidates(
        self, *, generated: int = 0, evaluated: int = 0, retained: int = 0
    ) -> None:
        """Add nonnegative operational candidate counts."""
        values = {
            "generated": _nonnegative_int(generated, "generated"),
            "evaluated": _nonnegative_int(evaluated, "evaluated"),
            "retained": _nonnegative_int(retained, "retained"),
        }
        with self._lock:
            self._counts.update(values)

    def record_cap_hit(self, name: str, count: int = 1) -> None:
        """Record an algorithmic budget or resource cap hit."""
        name = _label(name, "cap name")
        count = _nonnegative_int(count, "count")
        with self._lock:
            self._cap_hits[name] += count

    def record_unresolved_region(self, reason: str, count: int = 1) -> None:
        """Record a region the search could not resolve."""
        reason = _label(reason, "unresolved reason")
        count = _nonnegative_int(count, "count")
        with self._lock:
            self._unresolved[reason] += count

    def record_transfer(
        self,
        byte_count: int,
        direction: TransferDirection,
        device_id: int | str | None = None,
    ) -> None:
        """Record bytes crossing the host/device boundary or between devices."""
        byte_count = _nonnegative_int(byte_count, "byte_count")
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}.")
        normalized_device = _device_id(device_id)
        with self._lock:
            self._transfers[(direction, normalized_device)] += byte_count
            if normalized_device is not None and normalized_device not in self._device_ids:
                self._device_ids.append(normalized_device)

    def record_memory(self, *, cpu_bytes: int | None = None, gpu_bytes: int | None = None) -> None:
        """Record current memory usage in bytes when the caller has a measurement."""
        for field, value in (("cpu_bytes", cpu_bytes), ("gpu_bytes", gpu_bytes)):
            if value is not None:
                _nonnegative_int(value, field)
        with self._lock:
            if cpu_bytes is not None:
                self._memory["cpu_bytes"] = cpu_bytes
            if gpu_bytes is not None:
                self._memory["gpu_bytes"] = gpu_bytes

    def capture_memory(self, *, include_gpu: bool = False, device_id: int | None = None) -> None:
        """Capture process high-water memory and, optionally, an already-installed CuPy pool."""
        if type(include_gpu) is not bool:
            raise TypeError("include_gpu must be bool.")
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports KiB.
        cpu_bytes = int(usage if sys.platform == "darwin" else usage * 1024)
        gpu_bytes: int | None = None
        if include_gpu:
            cupy = sys.modules.get("cupy")
            if cupy is None:
                raise RuntimeError("GPU memory capture requires CuPy to be loaded already.")
            context = cupy.cuda.Device(device_id) if device_id is not None else _NullContext()
            with context:
                gpu_bytes = int(cupy.get_default_memory_pool().used_bytes())
        self.record_memory(cpu_bytes=cpu_bytes, gpu_bytes=gpu_bytes)

    def record_exception(self, exc: BaseException, *, stage: str | None = None) -> None:
        """Record exception type and stage without retaining potentially sensitive text."""
        if not isinstance(exc, BaseException):
            raise TypeError("exc must be an exception instance.")
        stage_name = _label(stage, "stage name") if stage is not None else None
        exception_type = _label(type(exc).__name__.lower(), "exception type")
        with self._lock:
            self._exceptions.append({"type": exception_type, "stage": stage_name})

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible snapshot using a fixed truth-free schema."""
        elapsed_s = _elapsed(self._started, self._clock())
        with self._lock:
            stages = [stage.as_dict() for stage in self._stages]
            counts = {name: self._counts[name] for name in ("generated", "evaluated", "retained")}
            caps = dict(sorted(self._cap_hits.items()))
            unresolved = dict(sorted(self._unresolved.items()))
            transfers = [
                {"direction": direction, "device_id": device, "bytes": count}
                for (direction, device), count in sorted(
                    self._transfers.items(), key=lambda item: (item[0][0], str(item[0][1]))
                )
            ]
            return {
                "schema_version": 1,
                "objective_version": self.objective_version,
                "backend": self.backend,
                "device_ids": list(self._device_ids),
                "elapsed_wall_s": elapsed_s,
                "stages": stages,
                "counts": counts,
                "cap_hits": caps,
                "unresolved_regions": unresolved,
                "transfers": transfers,
                "memory": dict(self._memory),
                "exceptions": [dict(item) for item in self._exceptions],
            }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize a telemetry snapshot to strict, portable JSON."""
        return json.dumps(self.as_dict(), allow_nan=False, indent=indent, sort_keys=True)


class _NullContext:
    def __enter__(self) -> _NullContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None
