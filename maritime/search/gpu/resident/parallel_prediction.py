"""GPU candidate/object-pair prediction with parallel grouping reductions.

The immutable input layout is shared with the resident implementation. The
spatial query is still one traversal per candidate, but expensive geodesic and
LOS queries run per candidate/object pair. Stable GPU sorting and parallel group
reductions replace insertion sorting and serial group scans. No host per-cell
computation or CPU fallback is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def _module(resident: Any) -> Any:
    module = getattr(resident, "_parallel_prediction_module", None)
    if module is None:
        folder = Path(__file__).parent
        source = (
            (folder / "kernels.cu").read_text()
            + "\n"
            + (folder / "parallel_prediction.cu").read_text()
        )
        module = resident.cp.RawModule(code=source, options=("--std=c++11",))
        resident._parallel_prediction_module = module
    return module


def predict_groups_parallel(resident: Any, cells: Any) -> dict[str, Any]:
    """Return the baseline prediction fields as device arrays.

    The caller supplies device cells on the resident device and must enter its
    stream. Aggregate pair count is read once to allocate a ragged workspace;
    no candidate values or matching results are reconstructed on the host.
    Capacity overflow follows the baseline's BVH-order prefix exactly, including
    flags recorded before the first overflow. Such candidates return zero groups
    and uncertainty bit 2. No nearby object is silently truncated.
    """
    cp, cfg, world = resident.cp, resident.config, resident.world
    if not isinstance(cells, cp.ndarray) or cells.ndim != 2 or cells.shape[1] != 4:
        raise TypeError("cells must be a (batch,4) device array")
    if cells.dtype != cp.float64 or cells.device.id != resident.device:
        raise ValueError("cells must be FP64 on the resident CUDA device")
    b, g = len(cells), cfg.object_capacity
    module = _module(resident)

    def launch(name: str, count: int, args: tuple[Any, ...]) -> None:
        if count:
            module.get_function(name)(((count + 127) // 128,), (128,), args)

    shape = (b, g)
    result = {
        "group_angles": cp.zeros(shape, dtype=cp.float64),
        "group_extents": cp.zeros(shape, dtype=cp.float64),
        "group_ids": cp.full(shape, -1, dtype=cp.int32),
        "group_exact": cp.zeros(shape, dtype=cp.int32),
        "group_counts": cp.zeros(b, dtype=cp.int32),
        "flags": cp.zeros(b, dtype=cp.int32),
        "point_states": cp.zeros(b, dtype=cp.int32),
    }
    if not b:
        return result
    context = cp.empty((b, 4), dtype=cp.float64)
    counts = cp.zeros(b, dtype=cp.int32)
    ob, oc, objects, on = resident.object_args
    radius = cfg.radius_m + cfg.range_numerical_margin_m
    launch(
        "pp_context",
        b,
        (
            cells,
            np.int32(b),
            *resident.map_args,
            ob,
            oc,
            on,
            np.float64(radius),
            np.float64(world.max_projection_scale or 1),
            context,
            counts,
            result["flags"],
            result["point_states"],
        ),
    )
    offsets = cp.empty(b + 1, dtype=cp.int64)
    offsets[0] = 0
    offsets[1:] = cp.cumsum(counts, dtype=cp.int64)
    pairs = int(offsets[-1].item())
    estimate = pairs * 64 + b * g * 256
    if estimate > cfg.device_workspace_budget_bytes:
        raise MemoryError("Parallel prediction exceeds the declared device workspace budget")
    if not pairs:
        return result
    pair_ids, owners, pair_exact, pair_flags = (cp.empty(pairs, dtype=cp.int32) for _ in range(4))
    pa, pd = (cp.empty(pairs, dtype=cp.float64) for _ in range(2))
    accepted = cp.empty(pairs, dtype=cp.uint8)
    launch(
        "pp_gather",
        b,
        (
            context,
            counts,
            offsets,
            np.int32(b),
            ob,
            oc,
            on,
            np.float64(radius),
            np.float64(world.max_projection_scale or 1),
            pair_ids,
            owners,
        ),
    )
    launch(
        "pp_pairs",
        pairs,
        (
            context,
            owners,
            pair_ids,
            np.int64(pairs),
            objects,
            *resident.map_args,
            np.float64(cfg.radius_m),
            np.float64(cfg.range_numerical_margin_m),
            pa,
            pd,
            pair_exact,
            accepted,
            pair_flags,
        ),
    )
    angles, ranges = (cp.full(shape, cp.inf, dtype=cp.float64) for _ in range(2))
    ids = cp.full(shape, np.iinfo(np.int32).max, dtype=cp.int32)
    exact = cp.zeros(shape, dtype=cp.int32)
    visible_counts = cp.zeros(b, dtype=cp.int32)
    launch(
        "pp_compact",
        b,
        (
            offsets,
            np.int32(b),
            np.int32(g),
            pair_ids,
            pa,
            pd,
            pair_exact,
            accepted,
            pair_flags,
            angles,
            ranges,
            ids,
            exact,
            visible_counts,
            result["flags"],
        ),
    )
    columns = cp.arange(g, dtype=cp.int32)[None, :]
    valid = columns < visible_counts[:, None]
    angles = cp.where(valid, angles, cp.inf)
    ranges = cp.where(valid, ranges, cp.inf)
    ids = cp.where(valid, ids, np.iinfo(np.int32).max)
    exact = cp.where(valid, exact, 0)
    # argsort is stable in the installed CuPy implementation. Sort ID first,
    # then bearing, to match the baseline's (bearing, catalog-row-ID) order.
    for order in (cp.argsort(ids, axis=1),):
        angles, ranges, ids, exact = (
            cp.take_along_axis(value, order, axis=1) for value in (angles, ranges, ids, exact)
        )
    order = cp.argsort(angles, axis=1)
    angles, ranges, ids, exact = (
        cp.take_along_axis(value, order, axis=1) for value in (angles, ranges, ids, exact)
    )
    safe_n = cp.maximum(visible_counts[:, None], 1)
    next_columns = cp.where(columns + 1 < visible_counts[:, None], columns + 1, 0)
    next_angles = cp.take_along_axis(angles, cp.broadcast_to(next_columns, shape), axis=1)
    # The baseline's one-object circular gap is zero, not 360 degrees.
    gaps = cp.where(valid, (next_angles - angles) % 360.0, 0.0)
    breaks = valid & (gaps > cfg.angular_resolution_deg)
    first_break = cp.min(cp.where(breaks, columns, g), axis=1)
    start = cp.where(first_break < g, (first_break + 1) % safe_n[:, 0], 0)
    rotation = cp.broadcast_to((columns + start[:, None]) % safe_n, shape)
    angles, ranges, ids, exact, gaps = (
        cp.take_along_axis(value, rotation, axis=1) for value in (angles, ranges, ids, exact, gaps)
    )
    end_group = valid & (
        (gaps > cfg.angular_resolution_deg) | (columns == visible_counts[:, None] - 1)
    )
    groups = cp.cumsum(end_group, axis=1, dtype=cp.int32) - end_group.astype(cp.int32)
    raw_groups = cp.sum(end_group, axis=1, dtype=cp.int32)
    boundary = valid & (
        cp.abs(gaps - cfg.angular_resolution_deg) <= 2 * cfg.bearing_numerical_margin_deg
    )
    result["flags"] |= cp.where(cp.any(boundary, axis=1), 8, 0).astype(cp.int32)
    minimum = cp.full(shape, cp.inf, dtype=cp.float64)
    certified = cp.ones(shape, dtype=cp.int32)
    representative = cp.full(shape, np.iinfo(np.int32).max, dtype=cp.int32)
    representative_angles = cp.zeros(shape, dtype=cp.float64)
    extent = cp.zeros(shape, dtype=cp.float64)
    ties = cp.zeros(shape, dtype=cp.int32)
    size_args = (np.int32(b), np.int32(g))
    launch(
        "pp_group_min",
        b * g,
        (ranges, exact, groups, visible_counts, *size_args, minimum, certified),
    )
    launch(
        "pp_group_rep",
        b * g,
        (ranges, ids, groups, visible_counts, *size_args, minimum, representative),
    )
    launch(
        "pp_group_angles",
        b * g,
        (
            angles,
            ranges,
            ids,
            groups,
            visible_counts,
            *size_args,
            minimum,
            representative,
            representative_angles,
        ),
    )
    launch(
        "pp_group_extent",
        b * g,
        (
            angles,
            ranges,
            ids,
            groups,
            visible_counts,
            *size_args,
            minimum,
            representative_angles,
            objects,
            np.float64(cfg.range_numerical_margin_m),
            extent,
            ties,
        ),
    )
    raw_valid = columns < raw_groups[:, None]
    result["flags"] |= cp.where(cp.any(raw_valid & (ties > 1), axis=1), 8, 0).astype(cp.int32)
    # The wrapper has pairs only when the catalog is nonempty; clip padding IDs
    # before the metadata gather, then exclude padding through raw_valid.
    safe_ids = cp.clip(representative, 0, len(world.objects) - 1)
    kinds = objects[:, 6][safe_ids]
    keep = raw_valid & (kinds != 2)
    positions = cp.cumsum(keep, axis=1, dtype=cp.int32) - 1
    rows = cp.broadcast_to(cp.arange(b)[:, None], shape)
    destination = rows[keep], positions[keep]
    result["group_angles"][destination] = representative_angles[keep]
    result["group_extents"][destination] = cp.minimum(180.0, extent[keep])
    result["group_ids"][destination] = representative[keep]
    result["group_exact"][destination] = ((certified == 1) & (kinds == 1))[keep].astype(cp.int32)
    result["group_counts"] = cp.sum(keep, axis=1, dtype=cp.int32)
    return result
