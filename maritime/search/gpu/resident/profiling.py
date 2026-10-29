"""Opt-in resident stream/host profiling without changing GPU computations.

CUDA events measure elapsed stream time including host launch gaps, not pure
kernel busy time. Nested inclusive durations overlap; exclusive durations
subtract direct children so their totals can be compared without double count.
No additional synchronization occurs until ``finish_profile``. Ready events
are written at the next prune call, after the engine's existing batch wait.
"""

from __future__ import annotations

import cProfile
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import get_ident
from time import perf_counter
from typing import Any
from uuid import uuid4

from . import chart_pruning, parallel_prediction
from .optimized import OptimizedResidentDevice

FORMAT = "ResidentStreamProfileV1"
STREAM_TIMING = "CUDA stream elapsed including host launch gaps; not pure kernel busy time"


@dataclass
class _Span:
    identifier: int
    parent_id: int | None
    label: str
    kind: str
    depth: int
    batch_index: int | None
    dimensions: dict[str, Any]
    host_start: float
    event_start: Any
    event_end: Any = None
    host_call_s: float = 0.0
    children: list[_Span] = field(default_factory=list)
    event_stream_ms: float | None = None
    error: str | None = None


def _argument_dimensions(grid: Any, block: Any, args: tuple[Any, ...]) -> dict[str, Any]:
    def launch_shape(value: Any) -> list[int]:
        if isinstance(value, int):
            return [value]
        return [int(part) for part in value]

    arrays = []
    for index, argument in enumerate(args):
        shape = getattr(argument, "shape", None)
        if shape is not None and len(shape):
            arrays.append(
                {
                    "index": index,
                    "shape": list(shape),
                    "dtype": str(getattr(argument, "dtype", "unknown")),
                }
            )
    return {"grid": launch_shape(grid), "block": launch_shape(block), "arrays": arrays}


class DeviceProfileCollector:
    """One worker/device owns its collector; finish only after its work stops."""

    def __init__(
        self,
        cupy_module: Any,
        stream: Any,
        device: int,
        profile_dir: Path,
        *,
        sample_every: int = 1,
        nvtx: bool = False,
    ) -> None:
        if type(sample_every) is not int or sample_every < 1:
            raise ValueError("sample_every must be a positive integer.")
        if type(nvtx) is not bool:
            raise TypeError("nvtx must be a bool.")
        self.cp, self.stream, self.device = cupy_module, stream, device
        self.sample_every = sample_every
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.profile_dir / f"gpu-{device}-spans.jsonl"
        self.summary_path = self.profile_dir / f"gpu-{device}-summary.json"
        # A repeated device instance must use a fresh directory, preserving evidence.
        self.raw_path.open("x").close()
        self.active = True
        self.batch_index: int | None = None
        self.prune_calls = 0
        self.sampled_batches = 0
        self._next_id = 0
        self._stack: list[_Span] = []
        self._pending: dict[int, _Span] = {}
        self._aggregates: dict[tuple[str, str], dict[str, Any]] = {}
        self._roots = defaultdict(float)
        self._span_count = 0
        self._event_count = 0
        self._query_count = 0
        self._flush_host_s = 0.0
        self._finished: dict[str, Any] | None = None
        self._host_profiler: cProfile.Profile | None = None
        self._host_thread: int | None = None
        self._nvtx = getattr(self.cp.cuda, "nvtx", None) if nvtx else None
        self._nvtx_error: str | None = None
        if nvtx and self._nvtx is None:
            self._nvtx_error = "NVTX is unavailable in the configured GPU runtime."

    def start_host_profile(self) -> None:
        """Enable in the worker thread after device/input construction finishes."""
        if self._host_profiler is not None:
            raise RuntimeError("Host profiling has already started.")
        self._host_thread = get_ident()
        self._host_profiler = cProfile.Profile()
        self._host_profiler.enable()

    def begin_batch(self) -> None:
        self.batch_index = self.prune_calls
        self.prune_calls += 1
        self.active = self._finished is None and self.batch_index % self.sample_every == 0
        self.sampled_batches += int(self.active)

    def before_seed(self) -> None:
        # Seeding belongs to the upcoming batch, before prune advances the counter.
        self.batch_index = self.prune_calls
        self.active = self._finished is None and self.batch_index % self.sample_every == 0

    @contextmanager
    def span(self, label: str, kind: str, dimensions: dict[str, Any] | None = None):
        if not self.active:
            yield
            return
        parent = self._stack[-1] if self._stack else None
        record = _Span(
            self._next_id,
            parent.identifier if parent else None,
            label,
            kind,
            len(self._stack),
            self.batch_index,
            dimensions or {},
            perf_counter(),
            self.cp.cuda.Event(),
        )
        self._next_id += 1
        self._event_count += 2
        record.event_start.record(self.stream)
        self._pending[record.identifier] = record
        if parent:
            parent.children.append(record)
        pushed = False
        if self._nvtx is not None:
            try:
                self._nvtx.RangePush(f"resident.gpu{self.device}.{label}")
                pushed = True
            except (AttributeError, ImportError, RuntimeError) as error:
                self._nvtx_error = str(error)
                self._nvtx = None
        self._stack.append(record)
        try:
            yield
        except BaseException as error:
            record.error = type(error).__name__
            raise
        finally:
            record.event_end = self.cp.cuda.Event()
            record.event_end.record(self.stream)
            record.host_call_s = perf_counter() - record.host_start
            self._stack.pop()
            if pushed:
                try:
                    self.cp.cuda.nvtx.RangePop()
                except (AttributeError, ImportError, RuntimeError) as error:
                    self._nvtx_error = str(error)
                    self._nvtx = None

    def _elapsed(self, record: _Span) -> float:
        if record.event_stream_ms is None:
            record.event_stream_ms = float(
                self.cp.cuda.get_elapsed_time(record.event_start, record.event_end)
            )
        return record.event_stream_ms

    def _row(self, record: _Span) -> dict[str, Any]:
        inclusive = self._elapsed(record)
        child_event = sum(self._elapsed(child) for child in record.children)
        child_host = sum(child.host_call_s for child in record.children)
        return {
            "schema": FORMAT,
            "device": self.device,
            "id": record.identifier,
            "parent_id": record.parent_id,
            "depth": record.depth,
            "batch_index": record.batch_index,
            "label": record.label,
            "kind": record.kind,
            "dimensions": record.dimensions,
            "host_start_perf_counter_s": record.host_start,
            "host_call_s": record.host_call_s,
            "host_exclusive_s": max(0.0, record.host_call_s - child_host),
            "event_stream_ms": inclusive,
            "event_stream_exclusive_ms": max(0.0, inclusive - child_event),
            "event_stream_includes_host_launch_gaps": True,
            "event_stream_is_kernel_busy": False,
            "error": record.error,
        }

    def _aggregate(self, row: dict[str, Any]) -> None:
        key = row["kind"], row["label"]
        aggregate = self._aggregates.setdefault(
            key, {"kind": key[0], "label": key[1], "count": 0, "error_count": 0}
        )
        aggregate["count"] += 1
        aggregate["error_count"] += int(row["error"] is not None)
        for name in (
            "host_call_s",
            "host_exclusive_s",
            "event_stream_ms",
            "event_stream_exclusive_ms",
        ):
            aggregate[f"{name}_total"] = aggregate.get(f"{name}_total", 0.0) + row[name]
            aggregate[f"{name}_max"] = max(aggregate.get(f"{name}_max", 0.0), row[name])
        if row["parent_id"] is None:
            self._roots["event_stream_ms"] += row["event_stream_ms"]
            self._roots["host_call_s"] += row["host_call_s"]
        self._roots["event_stream_exclusive_ms"] += row["event_stream_exclusive_ms"]
        self._roots["host_exclusive_s"] += row["host_exclusive_s"]
        self._span_count += 1

    def flush_ready(self) -> int:
        """Query completed events and append records without synchronizing."""
        if not self._pending:
            return 0
        begin = perf_counter()
        rows = []
        for identifier, record in list(self._pending.items()):
            if record.event_end is None:
                continue
            self._query_count += 1
            if not record.event_end.done:
                continue
            row = self._row(record)
            rows.append(row)
            self._aggregate(row)
            del self._pending[identifier]
        if rows:
            with self.raw_path.open("a") as stream:
                stream.write("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows))
        self._flush_host_s += perf_counter() - begin
        return len(rows)

    def _summary(self) -> dict[str, Any]:
        aggregates = []
        for (_, _), source in sorted(self._aggregates.items()):
            row = dict(source)
            for name in (
                "host_call_s",
                "host_exclusive_s",
                "event_stream_ms",
                "event_stream_exclusive_ms",
            ):
                row[f"{name}_mean"] = row[f"{name}_total"] / row["count"]
            aggregates.append(row)
        return {
            "schema": FORMAT,
            "device": self.device,
            "stream_timing_semantics": STREAM_TIMING,
            "exclusive_semantics": (
                "Inclusive duration minus direct nested children; sum exclusive values "
                "to avoid double count."
            ),
            "coverage": (
                "Only wrapped resident methods and explicit kernels; external scheduling, "
                "filtering and file I/O are covered only by optional host profiling."
            ),
            "sampling": {
                "every_n_batches": self.sample_every,
                "prune_calls": self.prune_calls,
                "sampled_batches": self.sampled_batches,
                "means_are_measured_samples_not_extrapolated": True,
            },
            "span_count": self._span_count,
            "event_count": self._event_count,
            "event_query_count": self._query_count,
            "profile_flush_host_s": self._flush_host_s,
            "root_and_exclusive_totals": dict(self._roots),
            "by_label": aggregates,
            "pending_spans": len(self._pending),
            "nvtx_error": self._nvtx_error,
            "host_profile_thread_ident": self._host_thread,
        }

    def finish(self) -> dict[str, Any]:
        """Synchronize once at finish, save event summary and optional pstats."""
        if self._finished is not None:
            return self._finished
        if self._stack:
            raise RuntimeError("Cannot finish a profile while an instrumented call is active.")
        host_path = None
        if self._host_profiler is not None:
            self._host_profiler.disable()
            host_path = self.profile_dir / f"gpu-{self.device}-host.pstats"
            temporary = host_path.with_name(host_path.name + f".{uuid4().hex}.pending")
            try:
                self._host_profiler.dump_stats(str(temporary))
                temporary.replace(host_path)
            finally:
                temporary.unlink(missing_ok=True)
        with self.cp.cuda.Device(self.device):
            self.stream.synchronize()
            self.flush_ready()
        if self._pending:
            raise RuntimeError(
                "Profile events remain incomplete after final stream synchronization."
            )
        summary = self._summary()
        temporary = self.summary_path.with_name(self.summary_path.name + f".{uuid4().hex}.pending")
        try:
            temporary.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
            temporary.replace(self.summary_path)
        finally:
            temporary.unlink(missing_ok=True)
        self.active = False
        self._finished = {
            "device": self.device,
            "raw_records": str(self.raw_path),
            "summary": str(self.summary_path),
            "host_profile": str(host_path) if host_path else None,
            "summary_data": summary,
        }
        return self._finished


class _KernelProxy:
    def __init__(
        self, kernel: Any, collector: DeviceProfileCollector, label: str, kind: str = "kernel"
    ) -> None:
        self._kernel, self._collector, self._label, self._kind = kernel, collector, label, kind

    def __call__(self, grid: Any, block: Any, args: tuple[Any, ...], **kwargs: Any) -> Any:
        if not self._collector.active:
            return self._kernel(grid, block, args, **kwargs)
        with self._collector.span(self._label, self._kind, _argument_dimensions(grid, block, args)):
            return self._kernel(grid, block, args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kernel, name)


class _ModuleProxy:
    def __init__(self, module: Any, collector: DeviceProfileCollector, label: str) -> None:
        self._module, self._collector, self._label = module, collector, label
        self._functions: dict[str, _KernelProxy] = {}

    def get_function(self, name: str) -> _KernelProxy:
        if name not in self._functions:
            self._functions[name] = _KernelProxy(
                self._module.get_function(name), self._collector, f"kernel.{self._label}.{name}"
            )
        return self._functions[name]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)


class ProfiledResidentDevice(OptimizedResidentDevice):
    """Instrument a resident instance; no global GPU functions are replaced.

    ``sample_every=10`` records every tenth whole batch including all its nested
    operations. Call ``finish_profile`` in the worker after its last work, or
    after the worker thread has exited. Host profiling covers that worker only.
    """

    def __init__(
        self,
        world: Any,
        config: Any,
        device: int,
        *,
        profile_dir: Path,
        sample_every: int = 1,
        host_profile: bool = False,
        nvtx: bool = False,
        **optimized_options: Any,
    ) -> None:
        if type(host_profile) is not bool:
            raise TypeError("host_profile must be a bool.")
        super().__init__(world, config, device, **optimized_options)
        self.profile = DeviceProfileCollector(
            self.cp, self.stream, device, profile_dir, sample_every=sample_every, nvtx=nvtx
        )
        self.module = _ModuleProxy(self.module, self.profile, "baseline")
        if self.use_parallel_prediction:
            module = parallel_prediction._module(self)
            self._parallel_prediction_module = _ModuleProxy(module, self.profile, "prediction")
        if self.use_independent_chart_pruning:
            module = chart_pruning.module_for(self, self.use_true_bearings)
            modules = getattr(self, "_independent_chart_modules")
            modules[self.use_true_bearings] = _ModuleProxy(module, self.profile, "chart")
        if not self.use_cooperative_matching:
            self.assignment = _KernelProxy(
                self.assignment, self.profile, "assignment.baseline", kind="assignment"
            )
        if host_profile:
            self.profile.start_host_profile()

    def seed(self, begin: int, end: int, ny: int) -> Any:
        self.profile.before_seed()
        if not self.profile.active:
            return super().seed(begin, end, ny)
        with self.profile.span("stage.seed", "stage", {"begin": begin, "end": end, "ny": ny}):
            return super().seed(begin, end, ny)

    def prune(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        self.profile.flush_ready()
        self.profile.begin_batch()
        if not self.profile.active:
            return super().prune(cells, bearings, tolerances)
        with self.profile.span("stage.prune", "stage", self._dimensions(cells, bearings)):
            return super().prune(cells, bearings, tolerances)

    def score(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        if not self.profile.active:
            return super().score(cells, bearings, tolerances)
        with self.profile.span("stage.score", "stage", self._dimensions(cells, bearings)):
            return super().score(cells, bearings, tolerances)

    def split(self, cells: Any) -> Any:
        if not self.profile.active:
            return super().split(cells)
        with self.profile.span("stage.split", "stage", self._dimensions(cells)):
            return super().split(cells)

    def _launch(self, name: str, n: int, args: tuple[Any, ...]) -> None:
        if not self.profile.active:
            return super()._launch(name, n, args)
        with self.profile.span(f"operation.{name}", "operation", {"count": n}):
            return super()._launch(name, n, args)

    def _cooperative_assignment(self, grid: Any, block: Any, args: tuple[Any, ...]) -> None:
        if not self.profile.active:
            return super()._cooperative_assignment(grid, block, args)
        with self.profile.span(
            "assignment.cooperative", "assignment", _argument_dimensions(grid, block, args)
        ):
            return super()._cooperative_assignment(grid, block, args)

    def _dimensions(self, cells: Any, bearings: Any = None) -> dict[str, int]:
        result = {
            "cells": len(cells),
            "object_capacity": self.config.object_capacity,
            "catalog_objects": len(self.world.objects),
        }
        if bearings is not None:
            result["reports"] = len(bearings)
        return result

    def finish_profile(self) -> dict[str, Any]:
        return self.profile.finish()


__all__ = ["DeviceProfileCollector", "ProfiledResidentDevice"]
