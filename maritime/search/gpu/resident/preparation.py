"""One-time CPU input preparation, explicitly outside the GPU search loop.

No observer truth is accepted. All positions are EPSG:32618 metres, paired with
WGS84 coordinates for GPU ellipsoidal bearings. Unknown support stays unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely import contains_properly
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2
from maritime.schemas.object_types import is_visual_entity, normalize_object_type
from maritime.search.deterministic_marker_scoring import _effective_position_uncertainty
from maritime.search.prediction import _visible_feature_offset


@dataclass(frozen=True)
class PackedTree:
    bounds: np.ndarray
    children: np.ndarray
    items: np.ndarray


@dataclass(frozen=True)
class PreparedWorld:
    layers: tuple[PackedTree, ...]  # land, coverage, unknown, chart completeness
    objects: np.ndarray  # x,y,lon,lat,uncertainty,extent,kind,identity-certified
    object_keys: tuple[str, ...]
    object_tree: PackedTree
    barriers: np.ndarray
    position_support_complete: bool
    coverage_present: bool
    chart_scope_certified: bool
    catalog_complete: bool
    identities_complete: bool
    max_projection_scale: float | None
    azimuth_error_deg: float | None
    crs: str = "EPSG:32618"


def _polygons(geometry: Any):
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _polygons(part)


def _edges(geometry: Any) -> np.ndarray:
    arrays = []
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            xy = np.asarray(ring.coords, dtype=np.float64)[:, :2]
            arrays.append(np.column_stack((xy[:-1], xy[1:])))
    return np.concatenate(arrays) if arrays else np.empty((0, 4), dtype=np.float64)


def pack_tree(items: np.ndarray, bounds: np.ndarray) -> PackedTree:
    """Balanced segment/point AABB tree. Leaf children store (-item-1, -1)."""
    items = np.ascontiguousarray(items, dtype=np.float64)
    bounds = np.asarray(bounds, dtype=np.float64).reshape((-1, 4))
    nodes: list[np.ndarray] = []
    children: list[tuple[int, int]] = []

    def build(indices: np.ndarray, depth: int = 0) -> int:
        if depth >= 60:
            raise ValueError("Spatial tree exceeds the GPU traversal stack capacity.")
        node = len(nodes)
        b = bounds[indices]
        extent = np.array([b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()])
        nodes.append(extent)
        children.append((-1, -1))
        if len(indices) == 1:
            children[node] = (-int(indices[0]) - 1, -1)
        else:
            axis = int((extent[3] - extent[1]) > (extent[2] - extent[0]))
            order = np.argsort((b[:, axis] + b[:, axis + 2]) * 0.5, kind="stable")
            cut = len(indices) // 2
            left = build(indices[order[:cut]], depth + 1)
            right = build(indices[order[cut:]], depth + 1)
            children[node] = (left, right)
        return node

    if len(items):
        build(np.arange(len(items)))
    return PackedTree(
        np.asarray(nodes, dtype=np.float64).reshape((-1, 4)),
        np.asarray(children, dtype=np.int32).reshape((-1, 2)),
        items,
    )


def _edge_tree(geometry: Any) -> PackedTree:
    e = _edges(geometry)
    b = np.column_stack(
        (
            np.minimum(e[:, 0], e[:, 2]),
            np.minimum(e[:, 1], e[:, 3]),
            np.maximum(e[:, 0], e[:, 2]),
            np.maximum(e[:, 1], e[:, 3]),
        )
    )
    return pack_tree(e, b)


def prepare_world(
    vector_map: VectorMap,
    catalog: CatalogV2,
    *,
    chart_geometry: Any = None,
    catalog_complete: bool = False,
    catalog_objects_cover_envelope: bool = False,
    identities_certified: bool = False,
    position_bound_m: float | None = None,
    exact_positions_asserted: bool = False,
    nominal_offsets_asserted: bool = False,
    max_projection_scale: float | None = None,
    azimuth_error_deg: float | None = None,
    land_barriers: tuple[tuple[float, float, float, float], ...] = (),
) -> PreparedWorld:
    """Pack immutable input once; CPU geometry never enters resident traversal.

    Position, identity, chart completeness and projection certificates are
    caller inputs. No certificate is inferred from object count or shoreline.
    Unsupported offsets stay unbounded. Larger per-record bounds win.
    """
    for name, value in (
        ("catalog_complete", catalog_complete),
        ("catalog_objects_cover_envelope", catalog_objects_cover_envelope),
        ("identities_certified", identities_certified),
        ("exact_positions_asserted", exact_positions_asserted),
        ("nominal_offsets_asserted", nominal_offsets_asserted),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name} must be a bool, not a truthy setting.")
    if vector_map.crs != CRS.from_epsg(32618):
        raise ValueError("Resident kernels currently support EPSG:32618 only.")
    for name, value in (
        ("max_projection_scale", max_projection_scale),
        ("azimuth_error_deg", azimuth_error_deg),
        ("position_bound_m", position_bound_m),
    ):
        if value is not None and (not isfinite(value) or value < 0):
            raise ValueError(f"{name} must be a finite nonnegative bound.")
    if max_projection_scale is not None and max_projection_scale < 1:
        raise ValueError("Projection-scale bound must be at least one.")
    if exact_positions_asserted and position_bound_m not in (None, 0):
        raise ValueError("Exact-position assertion conflicts with nonzero bound.")
    if chart_geometry is not None and (not chart_geometry.is_valid or chart_geometry.is_empty):
        raise ValueError("Chart coverage must be a nonempty valid polygonal geometry.")
    layers = tuple(
        _edge_tree(g)
        for g in (
            vector_map.land_geometry,
            vector_map._coverage,
            vector_map._unknown,
            chart_geometry,
        )
    )
    forward = Transformer.from_crs("EPSG:4326", vector_map.crs, always_xy=True)
    geod = Geod(ellps="WGS84")
    rows = []
    keys = []
    identity_tokens = []
    for record in sorted(catalog.records, key=lambda row: row.key):
        if not is_visual_entity(record.normalized_type or record.raw_type):
            continue
        kind_name = normalize_object_type(record.normalized_type or record.raw_type)
        kind = 1 if kind_name == "physical_aid" else 2 if kind_name == "vessel" else 0
        east, north, quality = _visible_feature_offset(record)
        lon, lat = record.longitude_deg, record.latitude_deg
        if quality == "calibrated" and (east != 0 or north != 0):
            from math import atan2, degrees, hypot

            lon, lat, _ = geod.fwd(lon, lat, degrees(atan2(east, north)), hypot(east, north))
        bound = _effective_position_uncertainty(
            record,
            bound_m=position_bound_m,
            exact_asserted=exact_positions_asserted,
        )
        if quality == "unsupported" or (quality != "calibrated" and not nominal_offsets_asserted):
            bound = None
        metadata = dict(record.metadata)
        entity = metadata.get("physical_entity_id") or dict(record.provenance).get(
            "physical_entity_id"
        )
        # Global certificate must also certify that remaining distinct keys are
        # distinct physical objects; repeated explicit physical IDs stay ambiguous.
        token = str(entity) if entity else record.key
        identity_tokens.append(token)
        x, y = forward.transform(lon, lat)
        if not all(isfinite(v) for v in (x, y, lon, lat)):
            raise ValueError("Object projection produced a nonfinite coordinate.")
        extent = float(metadata.get("angular_half_width_deg", 0.0) or 0.0)
        if not isfinite(extent) or not 0 <= extent <= 180:
            raise ValueError("Invalid chart-object angular extent.")
        rows.append(
            (
                x,
                y,
                lon,
                lat,
                float("inf") if bound is None else bound,
                extent,
                kind,
                int(identities_certified),
            )
        )
        keys.append(record.key)
    if len(set(keys)) != len(keys):
        raise ValueError(
            "Duplicate catalog keys require explicit reconciliation before preparation."
        )
    from collections import Counter

    counts = Counter(identity_tokens)
    for i, token in enumerate(identity_tokens):
        if counts[token] > 1:
            row = list(rows[i])
            row[7] = 0
            rows[i] = tuple(row)
    objects = np.asarray(rows, dtype=np.float64).reshape((-1, 8))
    xy = objects[:, :2]
    object_tree = pack_tree(objects, np.column_stack((xy, xy)))
    barriers = []
    for polygon in _polygons(vector_map.land_geometry):
        if not polygon.interiors and polygon.equals(box(*polygon.bounds)):
            barriers.append(polygon.bounds)
    for x0, x1, y0, y1 in land_barriers:
        candidate = box(x0, y0, x1, y1)
        if x0 >= x1 or y0 >= y1 or not contains_properly(vector_map.land_geometry, candidate):
            raise ValueError("Land barrier must be strictly inside known land.")
        barriers.append((x0, y0, x1, y1))
    return PreparedWorld(
        layers,
        objects,
        tuple(keys),
        object_tree,
        np.asarray(barriers, dtype=np.float64).reshape((-1, 4)),
        bool(np.isfinite(objects[:, 4]).all()),
        vector_map._coverage is not None,
        bool(chart_geometry is not None and catalog_complete and catalog_objects_cover_envelope),
        catalog_complete,
        bool(identities_certified and all(counts[v] == 1 for v in identity_tokens)),
        max_projection_scale,
        azimuth_error_deg,
    )
