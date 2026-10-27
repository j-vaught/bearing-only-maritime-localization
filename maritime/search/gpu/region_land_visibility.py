"""Conservative GPU proofs that known land blocks marker sight lines.

Only verified axis-aligned land rectangles serve as barriers. The rectangle
must separate the full observer cell from an exact marker, with strict
transverse and longitudinal margins. Unsupported cases remain unresolved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from typing import Any

import numpy as np
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_pruning import MarkerDetection, RegionCell, RegionEvidence


@dataclass(frozen=True, slots=True)
class LandVisibilityDecision:
    """Per-cell blockage certificate or unresolved result."""

    blocked: bool
    detection_index: int | None = None
    blocked_object_indices: tuple[int, ...] = ()
    reason: str = ""


def classify_land_blocking_cupy(
    cells: Sequence[RegionCell],
    detections: Sequence[MarkerDetection],
    evidence: Sequence[RegionEvidence],
    vector_map: VectorMap,
    *,
    compatible_object_masks: Sequence[Sequence[Sequence[bool]]] | None = None,
    certified_land_barriers_en_m: Sequence[tuple[float, float, float, float]] = (),
    cupy_module: Any | None = None,
    device_id: int = 0,
) -> tuple[LandVisibilityDecision, ...]:
    """Return per-cell universal blockage certificates computed on the GPU.

    A blocked decision proves every catalog object compatible with at least one
    positive detection is blocked from every point in the observer cell.
    Optional masks contain per-cell detection-by-object compatibility rows.
    Without masks, every catalog object is treated as possible for each
    detection. Complete chart and map scope, reliable projection, exact marker
    positions, and a positive detection are required. No center ray, negative
    evidence, or region ranking is used.

    Caller boxes can provide interior witnesses for nonrectangular land. The
    boxes are validated against known land, coverage, and unknown geometry,
    including polygon holes.
    """
    cell_rows = tuple(cells)
    detection_rows = tuple(detections)
    evidence_rows = tuple(evidence)
    if not isinstance(vector_map, VectorMap):
        raise TypeError("vector_map must be a VectorMap.")
    if len(cell_rows) != len(evidence_rows):
        raise ValueError("cells and evidence must have equal length.")
    if compatible_object_masks is not None and len(compatible_object_masks) != len(cell_rows):
        raise ValueError("compatible_object_masks must have one matrix per cell.")
    if any(not isinstance(c, RegionCell) for c in cell_rows):
        raise TypeError("cells must contain RegionCell values.")
    if any(not isinstance(d, MarkerDetection) for d in detection_rows):
        raise TypeError("detections must contain MarkerDetection values.")
    if any(not isinstance(e, RegionEvidence) for e in evidence_rows):
        raise TypeError("evidence must contain RegionEvidence values.")
    if type(device_id) is not int or device_id < 0:
        raise ValueError("device_id must be a nonnegative integer.")
    if not cell_rows:
        return ()
    if not detection_rows:
        return tuple(
            LandVisibilityDecision(False, reason="no positive marker detections") for _ in cell_rows
        )

    barriers = _certified_rectangular_land(vector_map, certified_land_barriers_en_m)
    if not barriers:
        return tuple(
            LandVisibilityDecision(False, reason="no certified land barrier") for _ in cell_rows
        )

    eligible = []
    for item in evidence_rows:
        eligible.append(
            item.chart_coverage_complete is True
            and item.map_evidence_covers_visibility_envelope is True
            and item.projection_reliable
            and not item.numeric_uncertainty
            and bool(item.objects)
            and all(
                obj.exact_position_asserted and obj.position_uncertainty_m in (None, 0.0)
                for obj in item.objects
            )
        )
    if not any(eligible):
        return tuple(
            LandVisibilityDecision(False, reason="no eligible exact marker evidence")
            for _ in cell_rows
        )

    cp = cupy_module if cupy_module is not None else import_module("cupy")
    bounds = np.asarray([c.bounds_en_m for c in cell_rows], dtype=np.float64)
    barrier_array = np.asarray(barriers, dtype=np.float64)
    output = [
        LandVisibilityDecision(False, reason="no universal land separator proved")
        for _ in cell_rows
    ]

    with cp.cuda.Device(device_id):
        cb = cp.asarray(bounds)
        bb = cp.asarray(barrier_array)
        tolerance = 1e-6
        for i, (item, ok) in enumerate(zip(evidence_rows, eligible, strict=True)):
            if not ok:
                continue
            obj = np.asarray([(o.east_m, o.north_m) for o in item.objects], dtype=np.float64)
            ob = cp.asarray(obj)
            x0, x1, y0, y1 = (cb[i, j] for j in range(4))
            bx0, bx1, by0, by1 = (bb[:, j] for j in range(4))
            ox, oy = ob[:, 0, None], ob[:, 1, None]
            vertical = (
                (bx1 - bx0 > tolerance)
                & (
                    ((x1 + tolerance < bx0) & (ox > bx1 + tolerance))
                    | ((ox + tolerance < bx0) & (x0 > bx1 + tolerance))
                )
                & (by0 + tolerance < y0)
                & (by1 > y1 + tolerance)
                & (by0 + tolerance < oy)
                & (by1 > oy + tolerance)
            )
            horizontal = (
                (by1 - by0 > tolerance)
                & (
                    ((y1 + tolerance < by0) & (oy > by1 + tolerance))
                    | ((oy + tolerance < by0) & (y0 > by1 + tolerance))
                )
                & (bx0 + tolerance < x0)
                & (bx1 > x1 + tolerance)
                & (bx0 + tolerance < ox)
                & (bx1 > ox + tolerance)
            )
            object_blocked = cp.any(vertical | horizontal, axis=1)
            blocked_cpu = tuple(bool(v) for v in cp.asnumpy(object_blocked))
            masks = None if compatible_object_masks is None else compatible_object_masks[i]
            if masks is not None and len(masks) != len(detection_rows):
                raise ValueError("each compatibility matrix must match detections.")
            for detection_index in range(len(detection_rows)):
                if masks is None:
                    compatible = tuple(range(len(item.objects)))
                else:
                    row = masks[detection_index]
                    if len(row) != len(item.objects):
                        raise ValueError("each compatibility row must match catalog objects.")
                    compatible = tuple(j for j, flag in enumerate(row) if bool(flag))
                if compatible and all(blocked_cpu[j] for j in compatible):
                    output[i] = LandVisibilityDecision(
                        True,
                        detection_index=detection_index,
                        blocked_object_indices=compatible,
                        reason=(
                            "every compatible marker is separated by known land"
                            " for the full observer cell"
                        ),
                    )
                    break
    return tuple(output)


def derive_certified_land_barrier_tiles(
    vector_map: VectorMap,
    bounds_en_m: tuple[float, float, float, float],
    *,
    tile_size_m: float,
    max_tiles: int,
) -> tuple[tuple[float, float, float, float], ...]:
    """Derive bounded interior rectangle witnesses from mapped polygon land.

    Bounds use (east_min, east_max, north_min, north_max). Only complete
    square tiles strictly inside known land, covered by the map, and disjoint
    from unknown geometry are returned. Grid size is checked against max_tiles
    before geometry work and raises ValueError when exceeded.
    """
    if not isinstance(vector_map, VectorMap):
        raise TypeError("vector_map must be a VectorMap.")
    if len(bounds_en_m) != 4 or not all(isfinite(v) for v in bounds_en_m):
        raise ValueError("bounds_en_m must contain four finite values.")
    x0, x1, y0, y1 = bounds_en_m
    if x0 >= x1 or y0 >= y1:
        raise ValueError("bounds_en_m must have positive area.")
    if not isfinite(tile_size_m) or tile_size_m <= 0:
        raise ValueError("tile_size_m must be finite and positive.")
    if type(max_tiles) is not int or max_tiles < 1:
        raise ValueError("max_tiles must be a positive integer.")
    nx = int(np.ceil((x1 - x0) / tile_size_m))
    ny = int(np.ceil((y1 - y0) / tile_size_m))
    if nx * ny > max_tiles:
        raise ValueError(f"tile grid requires {nx * ny} cells, exceeding max_tiles={max_tiles}.")
    coverage = getattr(vector_map, "_coverage", None)
    unknown = getattr(vector_map, "_unknown", None)
    land = vector_map.land_geometry
    if coverage is None or land is None or land.is_empty or not land.is_valid:
        return ()
    result = []
    for ix in range(nx):
        left = x0 + ix * tile_size_m
        right = min(left + tile_size_m, x1)
        for iy in range(ny):
            bottom = y0 + iy * tile_size_m
            top = min(bottom + tile_size_m, y1)
            tile = box(left, bottom, right, top)
            if not land.contains(tile) or not land.boundary.disjoint(tile):
                continue
            if not coverage.covers(tile):
                continue
            if unknown is not None and not unknown.disjoint(tile):
                continue
            result.append((left, right, bottom, top))
    return tuple(result)


def _certified_rectangular_land(
    vector_map: VectorMap,
    caller_boxes: Sequence[tuple[float, float, float, float]] = (),
) -> tuple[tuple[float, float, float, float], ...]:
    """Return rectangle witnesses wholly within known, covered land."""
    coverage = getattr(vector_map, "_coverage", None)
    unknown = getattr(vector_map, "_unknown", None)
    land = vector_map.land_geometry
    if coverage is None or land is None or land.is_empty or not land.is_valid:
        return ()
    result = []
    for polygon in _polygon_parts(land):
        if polygon.interiors:
            continue
        x0, y0, x1, y1 = polygon.bounds
        if not all(isfinite(v) for v in (x0, x1, y0, y1)) or x0 >= x1 or y0 >= y1:
            continue
        rectangle = box(x0, y0, x1, y1)
        if polygon.equals(rectangle) and coverage.covers(rectangle):
            if unknown is None or unknown.disjoint(rectangle):
                result.append((x0, x1, y0, y1))
    for raw in caller_boxes:
        if len(raw) != 4 or not all(isfinite(v) for v in raw):
            continue
        x0, x1, y0, y1 = raw
        if x0 >= x1 or y0 >= y1:
            continue
        candidate = box(x0, y0, x1, y1)
        if not land.contains(candidate) or not land.boundary.disjoint(candidate):
            continue
        if not coverage.covers(candidate):
            continue
        if unknown is not None and not unknown.disjoint(candidate):
            continue
        result.append((x0, x1, y0, y1))
    return tuple(dict.fromkeys(result))


def _polygon_parts(geometry: BaseGeometry):
    if isinstance(geometry, Polygon):
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _polygon_parts(part)


__all__ = [
    "LandVisibilityDecision",
    "classify_land_blocking_cupy",
    "derive_certified_land_barrier_tiles",
]
