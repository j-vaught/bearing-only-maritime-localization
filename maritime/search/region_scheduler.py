"""Bounded-batch multi-device traversal of every unpruned map region."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from math import ceil, isfinite

from maritime.search.gpu.region_pruning import RegionCell, RegionDecision

BatchEvaluator = Callable[[int, tuple[RegionCell, ...]], Sequence[RegionDecision]]


@dataclass(frozen=True, slots=True)
class RetainedRegion:
    """A terminal cell and its conservative decision."""

    cell: RegionCell
    decision: RegionDecision


@dataclass(frozen=True, slots=True)
class RegionTraversalResult:
    """Retained terminal cells plus an explicit unprocessed frontier."""

    retained: tuple[RetainedRegion, ...]
    unprocessed: tuple[RegionCell, ...]
    evaluated_count: int
    pruned_count: int
    device_evaluations: tuple[tuple[int, int], ...]
    prune_reasons: tuple[tuple[str, int], ...]

    @property
    def complete(self) -> bool:
        return not self.unprocessed


def make_initial_regions(
    bounds_en_m: tuple[float, float, float, float], *, cell_size_m: float = 10_000.0
) -> tuple[RegionCell, ...]:
    """Cover the complete rectangle with cells no larger than cell_size_m."""
    if not isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be finite and positive.")
    x0, x1, y0, y1 = bounds_en_m
    if not all(isfinite(v) for v in bounds_en_m) or x0 >= x1 or y0 >= y1:
        raise ValueError("bounds must be finite and have positive area.")
    nx, ny = ceil((x1 - x0) / cell_size_m), ceil((y1 - y0) / cell_size_m)
    return tuple(
        RegionCell(
            (
                x0 + ix * cell_size_m,
                min(x1, x0 + (ix + 1) * cell_size_m),
                y0 + iy * cell_size_m,
                min(y1, y0 + (iy + 1) * cell_size_m),
            )
        )
        for iy in range(ny)
        for ix in range(nx)
    )


def subdivide_region(cell: RegionCell, target_cell_size_m: float) -> tuple[RegionCell, ...]:
    """Split only dimensions that exceed the requested terminal size."""
    if not isfinite(target_cell_size_m) or target_cell_size_m <= 0:
        raise ValueError("target_cell_size_m must be finite and positive.")
    x0, x1, y0, y1 = cell.bounds_en_m
    xs = (x0, (x0 + x1) / 2, x1) if x1 - x0 > target_cell_size_m else (x0, x1)
    ys = (y0, (y0 + y1) / 2, y1) if y1 - y0 > target_cell_size_m else (y0, y1)
    if len(xs) == 2 and len(ys) == 2:
        return ()
    return tuple(
        RegionCell((xa, xb, ya, yb), level=cell.level + 1)
        for ya, yb in zip(ys[:-1], ys[1:], strict=True)
        for xa, xb in zip(xs[:-1], xs[1:], strict=True)
    )


def traverse_regions(
    initial_regions: Sequence[RegionCell],
    evaluate_batch: BatchEvaluator,
    *,
    device_ids: Sequence[int],
    target_cell_size_m: float,
    batch_size: int = 64,
    max_evaluations: int | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> RegionTraversalResult:
    """Process every surviving or unresolved cell, dispatching batches to idle devices.

    An optional evaluation limit returns the complete remaining frontier instead
    of discarding it. A failed device callback raises rather than hiding cells.
    The callback owns device selection and must return one ordered decision per
    input cell. Decisions may be conservative; unresolved cells are subdivided
    and ultimately retained if the terminal size is reached.
    """
    if not callable(evaluate_batch):
        raise TypeError("evaluate_batch must be callable.")
    devices = tuple(device_ids)
    if not devices or any(type(d) is not int or d < 0 for d in devices):
        raise ValueError("device_ids must be nonempty nonnegative integer IDs.")
    if len(set(devices)) != len(devices):
        raise ValueError("device_ids must be distinct.")
    if not isfinite(target_cell_size_m) or target_cell_size_m <= 0:
        raise ValueError("target_cell_size_m must be finite and positive.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be positive.")
    if max_evaluations is not None and (type(max_evaluations) is not int or max_evaluations < 1):
        raise ValueError("max_evaluations must be positive or None.")
    if any(not isinstance(cell, RegionCell) for cell in initial_regions):
        raise TypeError("initial_regions must contain RegionCell values.")
    frontier = deque(initial_regions)
    available = deque(devices)
    pending: dict[Future[Sequence[RegionDecision]], tuple[int, tuple[RegionCell, ...]]] = {}
    retained: list[RetainedRegion] = []
    counts: Counter[int] = Counter()
    reasons: Counter[str] = Counter()
    launched = evaluated = pruned = 0
    with ThreadPoolExecutor(max_workers=len(devices), thread_name_prefix="region-device") as pool:
        while frontier or pending:
            while (
                frontier and available and (max_evaluations is None or launched < max_evaluations)
            ):
                remaining = (
                    batch_size
                    if max_evaluations is None
                    else min(batch_size, max_evaluations - launched)
                )
                batch = tuple(frontier.popleft() for _ in range(min(remaining, len(frontier))))
                device = available.popleft()
                pending[pool.submit(evaluate_batch, device, batch)] = (device, batch)
                launched += len(batch)
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                device, batch = pending.pop(future)
                decisions = tuple(future.result())
                if len(decisions) != len(batch) or any(
                    not isinstance(d, RegionDecision)
                    or d.status not in ("prune", "survive", "unresolved")
                    for d in decisions
                ):
                    raise ValueError("evaluator must return one valid ordered decision per cell.")
                counts[device] += len(batch)
                evaluated += len(batch)
                for cell, decision in zip(batch, decisions, strict=True):
                    if decision.status == "prune":
                        pruned += 1
                        reasons[decision.reason] += 1
                        continue
                    children = subdivide_region(cell, target_cell_size_m)
                    if children:
                        frontier.extend(children)
                    else:
                        retained.append(RetainedRegion(cell, decision))
                available.append(device)
                if progress is not None:
                    progress(
                        evaluated, len(frontier) + sum(len(v[1]) for v in pending.values()), pruned
                    )
    retained.sort(key=lambda v: (v.cell.level, v.cell.bounds_en_m))
    return RegionTraversalResult(
        tuple(retained),
        tuple(frontier),
        evaluated,
        pruned,
        tuple((d, counts[d]) for d in devices),
        tuple(sorted(reasons.items())),
    )


__all__ = [
    "RetainedRegion",
    "RegionTraversalResult",
    "make_initial_regions",
    "subdivide_region",
    "traverse_regions",
]
