"""CuPy shoreline queries for polygonal coverage and unknown regions."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from math import isfinite
from typing import Any

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from maritime.geometry.vector_map import VectorMap, Visibility, WaterStatus

Point2D = tuple[float, float]


class GpuShorelineIndex:
    """GPU point-in-polygon and ray-edge queries over polygonal map layers.

    Land, coverage, and explicit unknown regions may contain polygons and
    holes. Boundary-degenerate queries are resolved through VectorMap.
    """

    def __init__(self, vector_map: VectorMap, *, cupy_module: Any | None = None) -> None:
        if not isinstance(vector_map, VectorMap):
            raise TypeError("vector_map must be a VectorMap instance.")
        coverage = vector_map._coverage
        layers = {
            "land": vector_map.land_geometry,
            "coverage": coverage,
            "unknown": vector_map._unknown,
        }
        self.vector_map = vector_map
        self._cp = cupy_module
        self._layer_edges: dict[str, np.ndarray] = {}
        self._layer_trees: dict[str, STRtree | None] = {}
        self._layer_bounds: dict[str, tuple[float, float, float, float] | None] = {}
        for name, geometry in layers.items():
            edges = (
                _segments(geometry) if geometry is not None else np.empty((0, 4), dtype=np.float64)
            )
            lines = tuple(LineString(((edge[0], edge[1]), (edge[2], edge[3]))) for edge in edges)
            self._layer_edges[name] = edges
            self._layer_trees[name] = STRtree(lines) if lines else None
            self._layer_bounds[name] = geometry.bounds if geometry is not None else None
        self._edges = self._layer_edges["land"]
        self._edge_tree = self._layer_trees["land"]
        self._edge_count = len(self._edges)
        self.point_gpu_count = 0
        self.point_cpu_fallback_count = 0
        self.visibility_gpu_count = 0
        self.visibility_cpu_fallback_count = 0

    def classify_candidates(
        self,
        candidate_xy_m: Sequence[Point2D],
        *,
        device_id: int = 0,
        chunk_size: int = 4096,
    ) -> tuple[WaterStatus, ...]:
        points = _points(candidate_xy_m, "candidate_xy_m")
        self._validate(device_id, chunk_size)
        if not points:
            return ()
        cp = self._cupy()
        result = np.empty(len(points), dtype=np.uint8)
        start = 0
        rows = min(chunk_size, len(points))
        while start < len(points):
            block = np.asarray(points[start : start + rows], dtype=np.float64)
            layer_blocks = {
                name: self._point_edges_for_batch(block, name)
                for name in ("land", "coverage", "unknown")
            }
            work = len(block) * sum(len(edges) for edges in layer_blocks.values())
            if work > 1_500_000 and rows > 1:
                rows = max(1, rows // 2)
                continue
            with cp.cuda.Device(device_id):
                codes, ambiguous = _gpu_point_codes(cp, block, layer_blocks)
                host_codes = cp.asnumpy(codes)
                host_ambiguous = cp.asnumpy(ambiguous)
            self.point_gpu_count += len(block)
            self.point_cpu_fallback_count += int(np.count_nonzero(host_ambiguous))
            for local, point in enumerate(block):
                if host_ambiguous[local]:
                    status = self.vector_map.point_water_status(tuple(point))
                    host_codes[local] = _water_code(status)
            result[start : start + len(block)] = host_codes
            start += len(block)
            rows = min(chunk_size, len(points) - start)
        return tuple(_water_status(int(code)) for code in result)

    def visibility_many(
        self,
        observer_xy_m: Point2D,
        targets_xy_m: Sequence[Point2D],
        *,
        device_id: int = 0,
        chunk_size: int = 1024,
        tolerance_m: float = 1e-6,
    ) -> tuple[Visibility, ...]:
        observer = _points((observer_xy_m,), "observer_xy_m")[0]
        targets = _points(targets_xy_m, "targets_xy_m")
        matrix = self.visibility_matrix(
            (observer,),
            targets,
            device_id=device_id,
            chunk_size=chunk_size,
            tolerance_m=tolerance_m,
        )
        return tuple(status for status in matrix[0] if status is not None)

    def visibility_matrix(
        self,
        observers_xy_m: Sequence[Point2D],
        targets_xy_m: Sequence[Point2D],
        *,
        in_range_mask: Sequence[Sequence[bool]] | np.ndarray | None = None,
        device_id: int = 0,
        chunk_size: int = 1024,
        tolerance_m: float = 1e-6,
        return_device_array: bool = False,
    ) -> tuple[tuple[Visibility | None, ...], ...] | Any:
        observers = _points(observers_xy_m, "observers_xy_m")
        targets = _points(targets_xy_m, "targets_xy_m")
        self._validate(device_id, chunk_size)
        if not isfinite(tolerance_m) or tolerance_m < 0:
            raise ValueError("tolerance_m must be finite and nonnegative.")
        if in_range_mask is None:
            mask = np.ones((len(observers), len(targets)), dtype=bool)
        else:
            mask = np.asarray(in_range_mask)
            if mask.shape != (len(observers), len(targets)) or mask.dtype != np.dtype(bool):
                raise ValueError("in_range_mask must be a boolean observer-by-target matrix.")
        pairs = [
            (i, j, (*observers[i], *targets[j]))
            for i in range(len(observers))
            for j in range(len(targets))
            if mask[i, j]
        ]
        output = np.full((len(observers), len(targets)), 3, dtype=np.uint8)
        if pairs:
            cp = self._cupy()
            start = 0
            rows = min(chunk_size, len(pairs))
            while start < len(pairs):
                block_pairs = pairs[start : start + rows]
                rays = np.asarray([item[2] for item in block_pairs], dtype=np.float64)
                layer_edges = {
                    name: self._edges_for_rays(rays, name)
                    for name in ("land", "coverage", "unknown")
                }
                endpoint_points = np.stack((rays[:, :2], rays[:, 2:]), axis=1).reshape((-1, 2))
                endpoint_edges = {
                    name: self._point_edges_for_batch(endpoint_points, name)
                    for name in ("land", "coverage", "unknown")
                }
                work = len(rays) * sum(len(edges) for edges in layer_edges.values())
                work += 2 * len(rays) * sum(len(edges) for edges in endpoint_edges.values())
                if work > 1_500_000 and rows > 1:
                    rows = max(1, rows // 2)
                    continue
                with cp.cuda.Device(device_id):
                    codes, ambiguous = _gpu_ray_codes(
                        cp, rays, layer_edges, endpoint_edges, tolerance_m
                    )
                    host_codes = cp.asnumpy(codes)
                    host_ambiguous = cp.asnumpy(ambiguous)
                self.visibility_gpu_count += len(block_pairs)
                self.visibility_cpu_fallback_count += int(np.count_nonzero(host_ambiguous))
                for local, (i, j, ray) in enumerate(block_pairs):
                    if host_ambiguous[local]:
                        status = self.vector_map.line_visibility(
                            (ray[0], ray[1]), (ray[2], ray[3]), tolerance_m=tolerance_m
                        )
                        host_codes[local] = _visibility_code(status)
                    output[i, j] = host_codes[local]
                start += len(block_pairs)
                rows = min(chunk_size, len(pairs) - start)
        if return_device_array:
            cp = self._cupy()
            with cp.cuda.Device(device_id):
                return cp.asarray(output)
        return tuple(
            tuple(None if code == 3 else _visibility_status(int(code)) for code in row)
            for row in output
        )

    def _point_edges_for_batch(self, points: np.ndarray, layer: str) -> np.ndarray:
        tree = self._layer_trees[layer]
        bounds = self._layer_bounds[layer]
        if tree is None or bounds is None or not len(points):
            return np.empty((0, 4), dtype=np.float64)
        xmax = max(float(bounds[2]), float(np.max(points[:, 0]))) + 1.0
        lines = np.asarray(
            [LineString(((float(x), float(y)), (xmax, float(y)))) for x, y in points],
            dtype=object,
        )
        hits = tree.query(lines)
        if hits.size == 0:
            return np.empty((0, 4), dtype=np.float64)
        selected = np.unique(hits[1]).astype(np.int64, copy=False)
        return self._layer_edges[layer][selected]

    def _edges_for_rays(self, rays: np.ndarray, layer: str) -> np.ndarray:
        tree = self._layer_trees[layer]
        if tree is None or not len(rays):
            return np.empty((0, 4), dtype=np.float64)
        lines = np.asarray(
            [
                LineString(((float(ox), float(oy)), (float(tx), float(ty))))
                for ox, oy, tx, ty in rays
            ],
            dtype=object,
        )
        hits = tree.query(lines)
        if hits.size == 0:
            return np.empty((0, 4), dtype=np.float64)
        selected = np.unique(hits[1]).astype(np.int64, copy=False)
        return self._layer_edges[layer][selected]

    def _cupy(self) -> Any:
        if self._cp is None:
            self._cp = import_module("cupy")
        return self._cp

    @staticmethod
    def _validate(device_id: int, chunk_size: int) -> None:
        if type(device_id) is not int or device_id < 0:
            raise ValueError("device_id must be a nonnegative integer.")
        if type(chunk_size) is not int or chunk_size < 1:
            raise ValueError("chunk_size must be positive.")


def _gpu_ring_state(cp: Any, points: np.ndarray, edges: np.ndarray) -> tuple[Any, Any]:
    """Return even-odd interior and closed-boundary masks for polygon rings."""
    p = cp.asarray(points)
    if not len(edges):
        zeros = cp.zeros(len(points), dtype=cp.bool_)
        return zeros, zeros
    e = cp.asarray(edges)
    x, y = p[:, 0, None], p[:, 1, None]
    x1, y1, x2, y2 = (e[:, i][None, :] for i in range(4))
    crosses = ((y1 > y) != (y2 > y)) & (
        x < (x2 - x1) * (y - y1) / cp.where(y2 == y1, 1.0, y2 - y1) + x1
    )
    inside = (cp.count_nonzero(crosses, axis=1) % 2) == 1
    dx, dy = x2 - x1, y2 - y1
    cross = (x - x1) * dy - (y - y1) * dx
    dot = (x - x1) * dx + (y - y1) * dy
    length_sq = dx * dx + dy * dy
    near = (
        (cp.abs(cross) <= 1e-8 * cp.maximum(1.0, cp.sqrt(length_sq)))
        & (dot >= 0)
        & (dot <= length_sq)
    )
    return inside, cp.any(near, axis=1)


def _gpu_point_codes(
    cp: Any, points: np.ndarray, layer_edges: dict[str, np.ndarray]
) -> tuple[Any, Any]:
    land_inside, land_boundary = _gpu_ring_state(cp, points, layer_edges["land"])
    coverage_inside, coverage_boundary = _gpu_ring_state(cp, points, layer_edges["coverage"])
    unknown_inside, unknown_boundary = _gpu_ring_state(cp, points, layer_edges["unknown"])
    covered = coverage_inside | coverage_boundary
    unknown = (unknown_inside | unknown_boundary) | ~covered
    land = land_inside & ~land_boundary & covered & ~unknown
    codes = cp.where(unknown, 2, cp.where(land, 0, 1)).astype(cp.uint8)
    ambiguous = land_boundary | coverage_boundary | unknown_boundary
    return codes, ambiguous


def _ray_interval_events(
    cp: Any, rays: np.ndarray, edges: np.ndarray, tolerance_m: float
) -> tuple[Any, Any, Any, Any]:
    """Return paired boundary intervals and degenerate-event flags."""
    r = cp.asarray(rays)
    if not len(edges):
        empty = cp.zeros((len(rays), 0), dtype=cp.float64)
        empty_mask = cp.zeros((len(rays), 0), dtype=cp.bool_)
        flags = cp.zeros(len(rays), dtype=cp.bool_)
        return empty, empty_mask, flags, flags
    e = cp.asarray(edges)
    ox, oy = r[:, 0, None], r[:, 1, None]
    dx, dy = r[:, 2, None] - ox, r[:, 3, None] - oy
    sx, sy = e[None, :, 2] - e[None, :, 0], e[None, :, 3] - e[None, :, 1]
    qx, qy = e[None, :, 0] - ox, e[None, :, 1] - oy
    den = dx * sy - dy * sx
    safe = cp.where(den == 0.0, 1.0, den)
    t = (qx * sy - qy * sx) / safe
    u = (qx * dy - qy * dx) / safe
    valid = (den != 0.0) & (t > 0.0) & (t < 1.0) & (u > 0.0) & (u < 1.0)
    ordered = cp.sort(cp.where(valid, t, 2.0), axis=1)
    pairs = ordered.shape[1] // 2
    starts = ordered[:, : 2 * pairs : 2]
    ends = ordered[:, 1 : 2 * pairs : 2]
    length = cp.sqrt(dx[:, 0] ** 2 + dy[:, 0] ** 2)[:, None] * (ends - starts)
    valid_intervals = (starts < 1.0) & (ends <= 1.0)
    scale = cp.maximum(1.0, cp.sqrt(dx * dx + dy * dy))
    parallel = cp.abs(den) <= 1e-12 * scale * cp.maximum(1.0, cp.sqrt(sx * sx + sy * sy))
    collinear = cp.abs(qx * dy - qy * dx) <= 1e-8 * scale
    ray_min_x, ray_max_x = (
        cp.minimum(r[:, 0, None], r[:, 2, None]),
        cp.maximum(r[:, 0, None], r[:, 2, None]),
    )
    ray_min_y, ray_max_y = (
        cp.minimum(r[:, 1, None], r[:, 3, None]),
        cp.maximum(r[:, 1, None], r[:, 3, None]),
    )
    edge_min_x, edge_max_x = (
        cp.minimum(e[None, :, 0], e[None, :, 2]),
        cp.maximum(e[None, :, 0], e[None, :, 2]),
    )
    edge_min_y, edge_max_y = (
        cp.minimum(e[None, :, 1], e[None, :, 3]),
        cp.maximum(e[None, :, 1], e[None, :, 3]),
    )
    overlap = (
        (ray_min_x <= edge_max_x + 1e-9)
        & (ray_max_x >= edge_min_x - 1e-9)
        & (ray_min_y <= edge_max_y + 1e-9)
        & (ray_max_y >= edge_min_y - 1e-9)
    )
    near_parallel = cp.any(parallel & collinear & overlap, axis=1)
    near_vertex = cp.any(
        (den != 0.0)
        & (t >= -1e-9)
        & (t <= 1 + 1e-9)
        & (u >= -1e-9)
        & (u <= 1 + 1e-9)
        & ((t < 1e-9) | (t > 1 - 1e-9) | (u < 1e-9) | (u > 1 - 1e-9)),
        axis=1,
    )
    threshold = max(1e-9, tolerance_m * 1e-8)
    near_tolerance = cp.any(valid_intervals & (cp.abs(length - tolerance_m) <= threshold), axis=1)
    near_zero = cp.any(valid_intervals & (length > 0) & (length <= 1e-8), axis=1)
    return length, valid_intervals, near_parallel | near_vertex | near_tolerance | near_zero, valid


def _gpu_ray_codes(
    cp: Any,
    rays: np.ndarray,
    layer_edges: dict[str, np.ndarray],
    endpoint_edges: dict[str, np.ndarray],
    tolerance_m: float,
) -> tuple[Any, Any]:
    endpoints = np.stack((rays[:, :2], rays[:, 2:]), axis=1).reshape((-1, 2))
    point_codes, point_ambiguous = _gpu_point_codes(cp, endpoints, endpoint_edges)
    point_codes = point_codes.reshape((-1, 2))
    land_lengths, land_intervals, land_ambiguous, _ = _ray_interval_events(
        cp, rays, layer_edges["land"], tolerance_m
    )
    unknown_lengths, unknown_intervals, unknown_ambiguous, _ = _ray_interval_events(
        cp, rays, layer_edges["unknown"], tolerance_m
    )
    coverage_lengths, coverage_intervals, coverage_ambiguous, _ = _ray_interval_events(
        cp, rays, layer_edges["coverage"], 0.0
    )
    land_cross = cp.any(land_intervals & (land_lengths > tolerance_m), axis=1)
    unknown_overlap = cp.any(unknown_intervals & (unknown_lengths > tolerance_m), axis=1)
    coverage_exit = cp.any(coverage_intervals & (coverage_lengths > 0.0), axis=1)
    observer_land = point_codes[:, 0] == 0
    observer_unknown = point_codes[:, 0] == 2
    target_land = point_codes[:, 1] == 0
    target_unknown = point_codes[:, 1] == 2
    blocked = observer_land | ((~observer_unknown) & (target_land | land_cross))
    unknown = observer_unknown | (
        (~observer_land) & (target_unknown | unknown_overlap | coverage_exit)
    )
    codes = cp.where(blocked, 0, cp.where(unknown, 2, 1)).astype(cp.uint8)
    ambiguous = (
        land_ambiguous
        | unknown_ambiguous
        | coverage_ambiguous
        | point_ambiguous.reshape((-1, 2)).any(axis=1)
    )
    return codes, ambiguous


def _join_segments(groups: Sequence[np.ndarray]) -> np.ndarray:
    populated = [group for group in groups if len(group)]
    return np.concatenate(populated, axis=0) if populated else np.empty((0, 4), dtype=np.float64)


def _segments(geometry: BaseGeometry) -> np.ndarray:
    edges: list[tuple[float, float, float, float]] = []
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            coords = np.asarray(ring.coords, dtype=np.float64)
            edges.extend(
                (float(a[0]), float(a[1]), float(b[0]), float(b[1]))
                for a, b in zip(coords[:-1], coords[1:], strict=True)
            )
    return np.asarray(edges, dtype=np.float64).reshape((-1, 4))


def _polygons(geometry: BaseGeometry):
    if isinstance(geometry, Polygon):
        yield geometry
    elif geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for item in geometry.geoms:
            yield from _polygons(item)


def _points(values: Sequence[Point2D], name: str) -> tuple[Point2D, ...]:
    result = []
    for value in values:
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"{name} entries must contain two finite coordinates.") from exc
        if not isfinite(x) or not isfinite(y):
            raise ValueError(f"{name} entries must contain two finite coordinates.")
        result.append((x, y))
    return tuple(result)


def _water_code(status: WaterStatus) -> int:
    return {"land": 0, "water": 1, "unknown": 2}[status]


def _water_status(code: int) -> WaterStatus:
    return {0: "land", 1: "water", 2: "unknown"}[code]


def _visibility_code(status: Visibility) -> int:
    return {"blocked": 0, "clear": 1, "unknown": 2}[status]


def _visibility_status(code: int) -> Visibility:
    return {0: "blocked", 1: "clear", 2: "unknown"}[code]
