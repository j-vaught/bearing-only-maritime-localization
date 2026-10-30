"""Experimental resident GPU traversal. Small synthetic checks only.

The host schedules root tasks, launches kernels, reads scalar progress and
writes results. It never evaluates map queries, bearings or assignments in the
search loop. There is no host computation fallback.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from importlib import import_module
from math import ceil, isfinite
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
from typing import Any

import numpy as np

from maritime.search.gpu.batch_assignment import _RAW_HUNGARIAN_SOURCE

from .preparation import PreparedWorld
from .session import ResidentSearchSession


def _terminal_status_codes(
    cp: Any, point_states: Any, positive_certified: Any, negative_certified: Any
) -> Any:
    """Keep a separate physical impossibility proof above nominal group matches."""
    return cp.where(
        point_states == 1,
        3,
        cp.where(negative_certified, 1, cp.where(positive_certified, 0, 2)),
    ).astype(cp.int32)


@dataclass(frozen=True)
class ResidentConfig:
    bounds_en_m: tuple[float, float, float, float]
    devices: tuple[int, ...]
    radius_m: float = 10_000.0
    initial_size_m: float = 10_000.0
    terminal_size_m: float = 100.0
    angular_resolution_deg: float = 0.1
    range_numerical_margin_m: float = 0.001
    bearing_numerical_margin_deg: float = 0.000001
    batch_cells: int = 128
    roots_per_task: int = 128
    object_capacity: int = 2048
    trusted_marker_labels: bool = False
    hard_bearing_bounds_asserted: bool = False
    numerical_validation_accepted: bool = False
    checkpoint_every_batches: int = 25
    device_workspace_budget_bytes: int = 2_000_000_000
    share_terminal_work: bool = False

    def __post_init__(self) -> None:
        for name in (
            "trusted_marker_labels",
            "hard_bearing_bounds_asserted",
            "numerical_validation_accepted",
            "share_terminal_work",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool, not a truthy setting.")
        for name in ("range_numerical_margin_m", "bearing_numerical_margin_deg"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive bound.")
        x0, x1, y0, y1 = self.bounds_en_m
        if not all(isfinite(v) for v in self.bounds_en_m) or x0 >= x1 or y0 >= y1:
            raise ValueError("Search bounds must be a finite nonempty rectangle.")
        if not self.devices or len(set(self.devices)) != len(self.devices):
            raise ValueError("Select distinct CUDA device IDs explicitly.")
        if any(type(d) is not int or d < 0 for d in self.devices):
            raise ValueError("CUDA device IDs must be nonnegative integers.")
        for v in (self.radius_m, self.initial_size_m, self.terminal_size_m):
            if not isfinite(v) or v <= 0:
                raise ValueError("Search sizes must be positive finite metres.")
        if self.terminal_size_m > self.initial_size_m:
            raise ValueError("Terminal cells cannot be larger than initial cells.")
        if not isfinite(self.angular_resolution_deg) or not 0 <= self.angular_resolution_deg < 180:
            raise ValueError("Angular resolution must be in [0,180).")
        for v in (
            self.batch_cells,
            self.roots_per_task,
            self.object_capacity,
            self.checkpoint_every_batches,
            self.device_workspace_budget_bytes,
        ):
            if type(v) is not int or v <= 0:
                raise ValueError(
                    "Batch, workspace and checkpoint limits must be positive integers."
                )


class ResidentDevice:
    """One device owns the map, catalog and computational workspaces."""

    def __init__(self, world: PreparedWorld, config: ResidentConfig, device: int) -> None:
        self._closed = False
        self.cp = import_module("cupy")
        self.world, self.config, self.device = world, config, device
        cp = self.cp
        with cp.cuda.Device(device):
            self.stream = cp.cuda.Stream(non_blocking=True)
            self.sharing_stream = None
            self.sharing_peers = set()
            if config.share_terminal_work:
                self.sharing_stream = cp.cuda.Stream(non_blocking=True)
                self.sharing_peers = set(config.devices) - {device}
            with self.stream:
                bounds, children, edges, offsets = [], [], [], []
                nb = ne = 0
                for layer in world.layers:
                    offsets.append((nb, ne, len(layer.bounds)))
                    bounds.append(layer.bounds)
                    children.append(layer.children)
                    edges.append(layer.items)
                    nb += len(layer.bounds)
                    ne += len(layer.items)
                self.map_args = (
                    cp.asarray(np.concatenate(bounds)),
                    cp.asarray(np.concatenate(children)),
                    cp.asarray(np.concatenate(edges)),
                    cp.asarray(np.asarray(offsets, dtype=np.int32)),
                )
                self.object_args = (
                    cp.asarray(world.object_tree.bounds),
                    cp.asarray(world.object_tree.children),
                    cp.asarray(world.objects),
                    np.int32(len(world.object_tree.bounds)),
                )
                self.barriers = cp.asarray(world.barriers)
                source = Path(__file__).with_name("kernels.cu").read_text()
                self.module = cp.RawModule(code=source, options=("--std=c++11",))
                self.assignment = cp.RawKernel(_RAW_HUNGARIAN_SOURCE, "batched_hungarian")
        self.marker_proofs = bool(
            world.chart_scope_certified
            and world.position_support_complete
            and world.max_projection_scale is not None
            and world.azimuth_error_deg is not None
            and config.trusted_marker_labels
            and config.hard_bearing_bounds_asserted
            and config.numerical_validation_accepted
        )
        self.exact_world = bool(
            world.position_support_complete and np.all(world.objects[:, 4] == 0)
        )
        self.max_uncertainty = float(np.max(world.objects[:, 4], initial=0))
        if not isfinite(self.max_uncertainty):
            self.max_uncertainty = 0.0  # No marker pruning with unbounded positions.

    def close(self) -> None:
        """Drop device-owned buffers and kernels in their CUDA context.

        Explicit field release breaks assignment-method cycles without touching
        the process-wide allocator or buffers owned by other residents.
        """
        if getattr(self, "_closed", False):
            return
        with self.cp.cuda.Device(self.device), self.stream:
            self.stream.synchronize()
            sharing_stream = getattr(self, "sharing_stream", None)
            if sharing_stream is not None:
                sharing_stream.synchronize()
            for name in (
                "map_args",
                "object_args",
                "barriers",
                "module",
                "assignment",
                "_parallel_prediction_module",
                "_independent_chart_modules",
                "sharing_stream",
            ):
                self.__dict__.pop(name, None)
            self._closed = True

    def _launch(self, name: str, n: int, args: tuple[Any, ...]) -> None:
        if n:
            self.module.get_function(name)(((n + 127) // 128,), (128,), args)

    def seed(self, begin: int, end: int, ny: int) -> Any:
        cp, cfg = self.cp, self.config
        cells = cp.empty((end - begin, 4), dtype=cp.float64)
        x0, x1, y0, y1 = cfg.bounds_en_m
        self._launch(
            "seed_cells",
            end - begin,
            (
                np.int64(begin),
                np.int64(end),
                np.int32(ny),
                np.float64(x0),
                np.float64(y0),
                np.float64(x1),
                np.float64(y1),
                np.float64(cfg.initial_size_m),
                cells,
            ),
        )
        return cells

    def prune(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        cp, w = self.cp, self.world
        state = cp.empty(len(cells), dtype=cp.int32)
        self._launch(
            "prune_cells",
            len(cells),
            (
                cells,
                np.int32(len(cells)),
                *self.map_args,
                *self.object_args,
                bearings,
                tolerances,
                np.int32(len(bearings)),
                self.barriers,
                np.int32(len(w.barriers)),
                np.float64(self.config.radius_m),
                np.float64(self.config.range_numerical_margin_m),
                np.float64(self.config.bearing_numerical_margin_deg),
                np.float64(w.max_projection_scale or 1),
                np.float64(w.azimuth_error_deg or 0),
                np.float64(self.max_uncertainty),
                np.int32(self.marker_proofs),
                np.int32(w.identities_complete),
                state,
            ),
        )
        return state

    def positive_marker_rejection(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
        """Optional certified positive-evidence proof; baseline retains its old gates."""
        return None

    def score(self, cells: Any, bearings: Any, tolerances: Any) -> dict[str, Any]:
        """Prediction, grouping, matching and residuals stay on this device."""
        cp, cfg, w = self.cp, self.config, self.world
        b, r, g = len(cells), len(bearings), cfg.object_capacity
        if r > 256:
            raise ValueError("At most 256 marker reports are supported; none may be truncated.")
        estimate = b * (r * g * 8 + (g + r + 1) * 40 + g * 64 + r * 24)
        if estimate > cfg.device_workspace_budget_bytes:
            raise MemoryError("Score batch exceeds the declared device workspace budget.")
        angles, ranges, ga, ge = (cp.empty((b, g), dtype=cp.float64) for _ in range(4))
        ids, exact, gi, gx = (cp.empty((b, g), dtype=cp.int32) for _ in range(4))
        gc, flags, point = (cp.empty(b, dtype=cp.int32) for _ in range(3))
        self._launch(
            "predict_groups",
            b,
            (
                cells,
                np.int32(b),
                *self.map_args,
                *self.object_args,
                np.float64(cfg.radius_m),
                np.float64(cfg.range_numerical_margin_m),
                np.float64(cfg.bearing_numerical_margin_deg),
                np.float64(w.max_projection_scale or 1),
                np.float64(cfg.angular_resolution_deg),
                np.int32(g),
                angles,
                ranges,
                ids,
                exact,
                ga,
                ge,
                gi,
                gx,
                gc,
                flags,
                point,
            ),
        )
        if r == 0:
            return dict(
                cells=cells,
                status=cp.where(point == 1, 3, 2),
                residual_deg=cp.full(b, cp.nan),
                flags=flags,
                matched_records=cp.empty((b, 0), dtype=cp.int32),
                marker_rejection_reason=cp.zeros(b, dtype=cp.int32),
            )
        valid = cp.arange(g)[None, :] < gc[:, None]
        ga, ge = cp.where(valid, ga, 0.0), cp.where(valid, ge, 0.0)
        gi, gx = cp.where(valid, gi, -1), cp.where(valid, gx, 0)
        residual = cp.abs((bearings[None, :, None] - ga[:, None, :] + 180.0) % 360.0 - 180.0)
        edge_limit = tolerances[None, :, None] + ge[:, None, :]
        # The gate combines one representative bearing and a two-bearing extent.
        decision_margin = 3 * cfg.bearing_numerical_margin_deg
        near_boundary = valid[:, None, :] & (cp.abs(residual - edge_limit) <= decision_margin)
        flags |= cp.where(cp.any(near_boundary, axis=(1, 2)), 8, 0).astype(cp.int32)
        cost = cp.where(
            valid[:, None, :] & (residual <= edge_limit + decision_margin),
            residual,
            cp.inf,
        )
        report_miss = cp.full((b, r), 180.0 * r + 1.0, dtype=cp.float64)
        group_miss, baseline = cp.zeros((b, g), dtype=cp.float64), cp.zeros(b, dtype=cp.float64)
        u = cp.empty((b, r + 1), dtype=cp.float64)
        v, minimum = (cp.empty((b, g + r + 1), dtype=cp.float64) for _ in range(2))
        p, way = (cp.empty((b, g + r + 1), dtype=cp.int32) for _ in range(2))
        used = cp.empty((b, g + r + 1), dtype=cp.uint8)
        cols = cp.empty((b, r), dtype=cp.int32)
        matched, unmatched, unused, total = (cp.empty(b, dtype=cp.float64) for _ in range(4))
        errors = cp.zeros(b, dtype=cp.int32)
        self.assignment(
            (b,),
            (1,),
            (
                cost,
                report_miss,
                group_miss,
                baseline,
                gc,
                np.int32(b),
                np.int32(r),
                np.int32(g),
                u,
                v,
                p,
                way,
                minimum,
                used,
                cols,
                matched,
                unmatched,
                unused,
                total,
                errors,
            ),
        )
        live = (cols >= 0) & (cols < gc[:, None])
        index = cp.clip(cols, 0, g - 1)
        exact_matches = cp.take_along_axis(gx, index, axis=1) == 1
        record_ids = cp.where(live, cp.take_along_axis(gi, index, axis=1), -1)
        complete = cp.all(live, axis=1)
        selected_residual = cp.take_along_axis(residual, index[:, :, None], axis=2)[:, :, 0]
        # A group extent is possible support, not a measured displacement of its
        # representative. Only a match inside the physical bearing tolerance can
        # certify a position; a numerical-boundary match remains unresolved.
        physical_match = selected_residual < tolerances[None, :] - decision_margin
        certain = (
            complete & cp.all(exact_matches & physical_match, axis=1) & (flags == 0) & (point == 0)
        )
        assumptions = bool(
            self.marker_proofs
            and getattr(self, "exact_marker_world", self.exact_world)
            and getattr(self, "marker_identities_complete", w.identities_complete)
        )
        # Known observer water and individually certified matched sight lines are
        # sufficient positive evidence. Unrelated shoreline outside those rays
        # need not fill a square surrounding the complete sensor view.
        certain &= assumptions
        rejection_reason = self.positive_marker_rejection(cells, bearings, tolerances)
        if rejection_reason is None:
            # Retain the legacy baseline's negative-evidence gates. Optimized
            # scoring replaces this with a separate conservative physical graph.
            chart_known = cp.empty(b, dtype=cp.int32)
            self._launch(
                "candidate_scope",
                b,
                (
                    cells,
                    np.int32(b),
                    *self.map_args,
                    np.float64(cfg.radius_m * (w.max_projection_scale or 1)),
                    chart_known,
                ),
            )
            impossible = (
                (~complete) & (flags == 0) & (point == 0) & (chart_known == 1) & assumptions
            )
            rejection_reason = cp.zeros(b, dtype=cp.int32)
        else:
            # Global nominal grouping/visibility warnings remain diagnostics.
            # They cannot invalidate an independently certified absence of any
            # injective explanation, including when another map area is unknown.
            impossible = (rejection_reason >= 2) & (rejection_reason <= 4)
        status = _terminal_status_codes(cp, point, certain, impossible)
        if bool(cp.any(errors != 0).item()):
            raise RuntimeError("CUDA assignment error; no fallback score was generated.")
        return dict(
            cells=cells,
            status=status,
            residual_deg=cp.where(complete, matched, cp.nan),
            flags=flags,
            matched_records=record_ids,
            marker_rejection_reason=rejection_reason,
        )

    def split(self, cells: Any) -> Any:
        cp = self.cp
        center = (cells[:, :2] + cells[:, 2:]) * 0.5
        children = cp.empty((len(cells), 4, 4), dtype=cp.float64)
        children[:, 0, :2], children[:, 0, 2:] = cells[:, :2], center
        children[:, 1, 0], children[:, 1, 1] = center[:, 0], cells[:, 1]
        children[:, 1, 2], children[:, 1, 3] = cells[:, 2], center[:, 1]
        children[:, 2, 0], children[:, 2, 1] = cells[:, 0], center[:, 1]
        children[:, 2, 2], children[:, 2, 3] = center[:, 0], cells[:, 3]
        children[:, 3, :2], children[:, 3, 2:] = center, cells[:, 2:]
        return children.reshape((-1, 4))


def _atomic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    temporary = path.with_suffix(".pending")
    with temporary.open("wb") as stream:
        np.savez(stream, allow_pickle=False, **arrays)
    temporary.replace(path)


def _run_resident_search(
    world: PreparedWorld,
    config: ResidentConfig,
    bearings_deg: np.ndarray,
    tolerances_deg: np.ndarray,
    output_dir: Path,
    *,
    resume: bool = False,
    device_factory: Any | None = None,
    session: ResidentSearchSession | None = None,
    sharing: bool | None = None,
) -> dict[str, Any]:
    """All per-cell work runs on the devices; results apply to centers only.

    Geometry and catalog preparation is outside this API. Periodic frontier
    snapshots and leaf outputs are preserved. Automatic resume is not implemented.
    """
    if resume:
        raise NotImplementedError(
            "Automatic resume is pending validation; snapshots are preserved."
        )
    factory = ResidentDevice if device_factory is None else device_factory
    enabled = config.share_terminal_work if sharing is None else sharing
    if type(enabled) is not bool or (enabled and not config.share_terminal_work):
        raise ValueError("Work sharing must be explicitly enabled in the fixed configuration")
    if not callable(factory):
        raise TypeError("device_factory must construct a resident device.")
    bearings = np.asarray(bearings_deg, dtype=np.float64)
    tolerances = np.asarray(tolerances_deg, dtype=np.float64)
    if bearings.ndim != 1 or bearings.shape != tolerances.shape or len(bearings) > 256:
        raise ValueError("Aligned marker vectors are required, at most 256 reports.")
    if not np.isfinite(bearings).all() or not np.isfinite(tolerances).all():
        raise ValueError("Marker observations must be finite.")
    if np.any((bearings < 0) | (bearings >= 360)) or np.any((tolerances < 0) | (tolerances >= 180)):
        raise ValueError("Invalid marker bearing or tolerance.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = dict(
        schema="ResidentSearchV1",
        implementation_status="resident_gpu",
        config=asdict(config),
        dynamic_score_sharing_enabled=enabled,
        cpu_roles=[
            "input_preparation",
            "kernel_launch",
            "root_task_scheduling",
            "scalar_progress",
            "file_io",
        ],
        cpu_query_fallback_available=False,
        association_uniqueness_evaluated=False,
        sample_semantics="terminal cell centers only; all terminal cells retained",
        completeness_semantics="all roots and descendants, no top-N",
        result_status_codes={
            "0": "consistent",
            "1": "inconsistent_center_only",
            "2": "unresolved",
            "3": "land_center_only",
        },
        gpu_flag_bits={
            "1": "uncertain_geometry",
            "2": "object_capacity_exceeded",
            "4": "numerical_failure",
            "8": "numerical_boundary",
        },
        marker_rejection_reasons={
            "0": "no certified physical-positive rejection",
            "2": "positive marker has no possible object",
            "3": "too few distinct possible objects",
            "4": "no injective positive-marker explanation",
        },
        uncertainty_semantics=(
            "Nominal flags remain diagnostics. An independent certified physical-positive "
            "rejection may establish inconsistent status despite unrelated nominal flags."
        ),
        positive_marker_model=(
            "Distinct physical representatives with bounded bearing error; group extent "
            "does not certify an arbitrary representative bearing displacement."
        ),
        position_support_complete=world.position_support_complete,
        chart_scope_certified=world.chart_scope_certified,
        marker_bearings_deg=bearings.tolist(),
        marker_tolerances_deg=tolerances.tolist(),
        object_keys=world.object_keys,
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    x0, x1, y0, y1 = config.bounds_en_m
    nx, ny = ceil((x1 - x0) / config.initial_size_m), ceil((y1 - y0) / config.initial_size_m)
    total_roots, next_root = nx * ny, 0
    control_lock, stop = Lock(), Event()
    start_time = perf_counter()
    totals = dict(evaluated=0, pruned=0, terminal=0, completed_roots=0)
    active_roots = 0
    broker = None
    if enabled:
        from .work_sharing import ScoreBroker

        broker = ScoreBroker(config.devices)

    def receive_score(resident: Any, device: int, packet: Any, db: Any, dt: Any) -> None:
        cp = resident.cp
        copy_begin, copy_end = cp.cuda.Event(), cp.cuda.Event()
        score_begin, score_end = cp.cuda.Event(), cp.cuda.Event()
        copy_ms = score_ms = 0.0
        assert broker is not None
        try:
            packet.ready.synchronize()
            with cp.cuda.Device(packet.source):
                copy_ms = float(cp.cuda.get_elapsed_time(packet.d2h_begin, packet.ready))
            with resident.sharing_stream:
                copy_begin.record(resident.sharing_stream)
                cells = cp.asarray(packet.staging, blocking=False)
                copy_end.record(resident.sharing_stream)
            resident.stream.wait_event(copy_end)
            hook = getattr(resident, "begin_remote_score", None)
            if hook is not None:
                hook()
            score_begin.record(resident.stream)
            scored = resident.score(cells, db, dt)
            score_end.record(resident.stream)
            score_end.synchronize()
            copy_ms += float(cp.cuda.get_elapsed_time(copy_begin, copy_end))
            score_ms = float(cp.cuda.get_elapsed_time(score_begin, score_end))
            folder = output_dir / f"roots-{packet.begin}-{packet.end}-gpu-{device}"
            folder.mkdir(exist_ok=True)
            _atomic_npz(
                folder / f"leaf-peer-{packet.identifier:08d}.npz",
                {key: cp.asnumpy(value) for key, value in scored.items()},
            )
        except BaseException as error:
            broker.finish(packet, copy_ms=copy_ms, score_ms=score_ms, error=error)
            raise
        else:
            broker.finish(packet, copy_ms=copy_ms, score_ms=score_ms)

    def worker(device: int, resident: Any | None = None) -> None:
        nonlocal next_root, active_roots
        if resident is None:
            resident = factory(world, config, device)
        cp = resident.cp
        with cp.cuda.Device(device), resident.stream:
            db, dt = cp.asarray(bearings), cp.asarray(tolerances)
            batch_id = 0
            while not stop.is_set():
                with control_lock:
                    begin = next_root
                    if begin >= total_roots:
                        if broker is None or active_roots == 0:
                            return
                        end = begin
                    else:
                        end = min(total_roots, begin + config.roots_per_task)
                        next_root = end
                        active_roots += 1
                if begin == end:
                    assert broker is not None
                    broker.mark_idle(device)
                    packet = broker.take(device)
                    if packet is None:
                        broker.pause()
                    else:
                        receive_score(resident, device, packet, db, dt)
                    continue
                queue = resident.seed(begin, end, ny)
                task_dir = output_dir / f"roots-{begin}-{end}-gpu-{device}"
                task_dir.mkdir(exist_ok=True)
                handed_off = []
                while len(queue) and not stop.is_set():
                    batch, queue = queue[: config.batch_cells], queue[config.batch_cells :]
                    event_start, event_end = cp.cuda.Event(), cp.cuda.Event()
                    event_start.record(resident.stream)
                    states = resident.prune(batch, db, dt)
                    survivor = batch[states == 0]
                    small = (
                        cp.max(survivor[:, 2:] - survivor[:, :2], axis=1) <= config.terminal_size_m
                    )
                    terminal, expand = survivor[small], survivor[~small]
                    queue = cp.concatenate((queue, resident.split(expand)))
                    terminal_count = len(terminal)
                    if broker is not None and terminal_count:
                        packets = broker.reserve(
                            device, terminal_count, begin, end, resident.sharing_peers
                        )
                        if packets:
                            ready = cp.cuda.Event()
                            ready.record(resident.stream)
                            offset = terminal_count
                            for packet in packets:
                                start = offset - packet.count
                                payload = terminal[start:offset]
                                pinned = cp.cuda.alloc_pinned_memory(payload.nbytes)
                                staging = np.frombuffer(
                                    pinned, dtype=np.float64, count=payload.size
                                ).reshape(payload.shape)
                                d2h_begin, d2h_end = cp.cuda.Event(), cp.cuda.Event()
                                sharing_stream = resident.sharing_stream
                                assert sharing_stream is not None
                                with sharing_stream:
                                    sharing_stream.wait_event(ready)
                                    d2h_begin.record(resident.sharing_stream)
                                    cp.asnumpy(
                                        payload,
                                        out=staging,
                                        stream=resident.sharing_stream,
                                        blocking=False,
                                    )
                                    d2h_end.record(resident.sharing_stream)
                                broker.publish(
                                    packet,
                                    payload,
                                    d2h_end,
                                    staging=staging,
                                    pinned=pinned,
                                    d2h_begin=d2h_begin,
                                )
                                offset = start
                            terminal = terminal[:offset]
                            handed_off.extend(packets)
                    score_begin = cp.cuda.Event() if broker is not None and len(terminal) else None
                    if score_begin is not None:
                        score_begin.record(resident.stream)
                    scored = resident.score(terminal, db, dt) if len(terminal) else None
                    event_end.record(resident.stream)
                    event_end.synchronize()
                    elapsed_gpu_ms = float(cp.cuda.get_elapsed_time(event_start, event_end))
                    if score_begin is not None:
                        assert broker is not None
                        broker.observe_local(
                            len(terminal), float(cp.cuda.get_elapsed_time(score_begin, event_end))
                        )
                    pruned = int(cp.count_nonzero(states).item())
                    if scored is not None:
                        _atomic_npz(
                            task_dir / f"leaf-{batch_id:08d}.npz",
                            {k: cp.asnumpy(v) for k, v in scored.items()},
                        )
                    if batch_id % config.checkpoint_every_batches == 0:
                        _atomic_npz(task_dir / "frontier.npz", {"cells": cp.asnumpy(queue)})
                    with control_lock:
                        totals["evaluated"] += len(batch)
                        totals["pruned"] += pruned
                        totals["terminal"] += terminal_count
                        progress = dict(
                            stage="resident_search",
                            device=device,
                            **totals,
                            active_task_frontier=len(queue),
                            batch_gpu_stream_elapsed_ms=elapsed_gpu_ms,
                            elapsed_s=perf_counter() - start_time,
                            assigned_roots=next_root,
                            total_roots=total_roots,
                        )
                        with (output_dir / "progress.jsonl").open("a") as stream:
                            stream.write(json.dumps(progress) + "\n")
                    batch_id += 1
                if not len(queue):
                    if broker is not None:
                        for packet in handed_off:
                            broker.wait(packet, stop)
                    (task_dir / "complete.json").write_text(
                        json.dumps({"begin": begin, "end": end}) + "\n"
                    )
                    with control_lock:
                        totals["completed_roots"] += end - begin
                        active_roots -= 1
                else:
                    _atomic_npz(task_dir / "frontier.npz", {"cells": cp.asnumpy(queue)})

    def one_shot_worker(device: int) -> None:
        resident = factory(world, config, device)
        cp = resident.cp
        with cp.cuda.Device(device), resident.stream:
            begin = getattr(resident, "begin_search", None)
            end = getattr(resident, "end_search", None)
            if begin is not None:
                begin(output_dir)
            try:
                worker(device, resident)
            finally:
                resident.stream.synchronize()
                if end is not None:
                    end()

    try:
        if session is not None:
            session.execute_workers(worker, output_dir, stop)
        else:
            with ThreadPoolExecutor(max_workers=len(config.devices)) as pool:
                futures = [pool.submit(one_shot_worker, d) for d in config.devices]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except BaseException:
                        stop.set()
                        raise
    except BaseException:
        (output_dir / "failed.json").write_text(json.dumps(dict(totals, complete=False)) + "\n")
        raise
    summary: dict[str, Any] = dict(
        totals,
        complete=totals["completed_roots"] == total_roots,
        elapsed_s=perf_counter() - start_time,
        accuracy_evaluated=False,
        numerical_validation_accepted=config.numerical_validation_accepted,
    )
    if session is not None:
        summary["resident_session"] = {
            "request_number": session.completed_requests + 1,
            "devices": list(session.last_device_diagnostics.values()),
            "map_buffers_retained": True,
        }
    if broker is not None:
        summary["dynamic_score_sharing"] = broker.summary()
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run_resident_search(
    world: PreparedWorld,
    config: ResidentConfig,
    bearings_deg: np.ndarray,
    tolerances_deg: np.ndarray,
    output_dir: Path,
    *,
    resume: bool = False,
    device_factory: Any | None = None,
    session: ResidentSearchSession | None = None,
    sharing: bool | None = None,
) -> dict[str, Any]:
    """Search with fresh outputs, optionally retaining devices between requests.

    Session construction does not execute a search. Each invocation preserves
    every surviving terminal center.
    """
    if session is None:
        return _run_resident_search(
            world,
            config,
            bearings_deg,
            tolerances_deg,
            output_dir,
            resume=resume,
            device_factory=device_factory,
            sharing=sharing,
        )
    if device_factory is not None:
        raise ValueError("Specify device_factory on the session, not each request.")
    with session.request(world, config):
        return _run_resident_search(
            world,
            config,
            bearings_deg,
            tolerances_deg,
            output_dir,
            resume=resume,
            session=session,
            sharing=sharing,
        )
