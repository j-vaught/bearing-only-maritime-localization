"""Conservative region pruning using positive deterministic marker detections."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

import numpy as np

RegionStatus = Literal["survive", "prune", "unresolved"]


@dataclass(frozen=True, slots=True)
class RegionCell:
    """Projected rectangle in (east_min, east_max, north_min, north_max) order."""

    bounds_en_m: tuple[float, float, float, float]
    level: int = 0

    def __post_init__(self) -> None:
        x0, x1, y0, y1 = self.bounds_en_m
        if not all(isfinite(v) for v in self.bounds_en_m) or x0 >= x1 or y0 >= y1:
            raise ValueError("region bounds must be finite and have positive area.")
        if type(self.level) is not int or self.level < 0:
            raise ValueError("level must be a nonnegative integer.")

    @property
    def center_xy_m(self) -> tuple[float, float]:
        x0, x1, y0, y1 = self.bounds_en_m
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    @property
    def covering_radius_m(self) -> float:
        x0, x1, y0, y1 = self.bounds_en_m
        return hypot((x1 - x0) / 2, (y1 - y0) / 2)

    def visibility_envelope(self, radius_m: float) -> tuple[float, float, float, float]:
        if not isfinite(radius_m) or radius_m < 0:
            raise ValueError("visibility radius must be finite and nonnegative.")
        x0, x1, y0, y1 = self.bounds_en_m
        return (x0 - radius_m, x1 + radius_m, y0 - radius_m, y1 + radius_m)


@dataclass(frozen=True, slots=True)
class RegionObject:
    """Catalog marker; identity must be proven before count reasoning."""

    key: str
    east_m: float
    north_m: float
    position_uncertainty_m: float | None = None
    identity_proven: bool = False
    exact_position_asserted: bool = False

    def __post_init__(self) -> None:
        if not self.key or not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("object key and coordinates must be valid.")
        if self.position_uncertainty_m is not None and (
            not isfinite(self.position_uncertainty_m) or self.position_uncertainty_m < 0
        ):
            raise ValueError("position uncertainty must be finite and nonnegative.")


@dataclass(frozen=True, slots=True)
class MarkerDetection:
    """Positive deterministic marker bearing and admitted tolerance."""

    bearing_deg: float
    tolerance_deg: float

    def __post_init__(self) -> None:
        if isfinite(self.bearing_deg) and self.bearing_deg == 360.0:
            object.__setattr__(self, "bearing_deg", 0.0)
        if (
            not isfinite(self.bearing_deg)
            or not 0 <= self.bearing_deg < 360
            or not isfinite(self.tolerance_deg)
            or not 0 <= self.tolerance_deg < 180
        ):
            raise ValueError("detection bearing or tolerance is invalid.")


@dataclass(frozen=True, slots=True)
class RegionEvidence:
    """Evidence state explicitly scoped to the complete cell visibility envelope."""

    chart_coverage_complete: bool | None
    land_fully_covers: bool | None
    map_evidence_covers_visibility_envelope: bool
    projection_reliable: bool
    numeric_uncertainty: bool
    objects: tuple[RegionObject, ...] = ()
    certified_max_projection_scale: float | None = None


@dataclass(frozen=True, slots=True)
class RegionDecision:
    status: RegionStatus
    reason: str
    compatible_object_masks: tuple[tuple[bool, ...], ...] = ()


def classify_region(
    cell: RegionCell,
    detections: Sequence[MarkerDetection],
    evidence: RegionEvidence,
    *,
    visibility_radius_m: float,
    minimum_distinct_markers: int | None = None,
    certified_azimuth_error_deg: float | None = None,
) -> RegionDecision:
    """Classify safely; angular mismatch is unresolved without certified bounds."""
    _validate_region_inputs(
        (cell,),
        detections,
        (evidence,),
        visibility_radius_m,
        minimum_distinct_markers,
        certified_azimuth_error_deg,
    )
    if minimum_distinct_markers is not None and (
        type(minimum_distinct_markers) is not int or minimum_distinct_markers < 1
    ):
        raise ValueError("minimum_distinct_markers must be positive or None.")
    if certified_azimuth_error_deg is not None and (
        not isfinite(certified_azimuth_error_deg) or certified_azimuth_error_deg < 0
    ):
        raise ValueError("certified azimuth error must be finite and nonnegative.")
    if not evidence.projection_reliable or evidence.numeric_uncertainty:
        return RegionDecision(
            "unresolved", "projection or numeric uncertainty prevents a safe bound"
        )
    if evidence.land_fully_covers is True:
        return RegionDecision("prune", "reliable known land covers the entire observer cell")
    if not evidence.map_evidence_covers_visibility_envelope:
        return RegionDecision(
            "unresolved", "map evidence does not cover the full visibility envelope"
        )
    if evidence.land_fully_covers is None:
        return RegionDecision("unresolved", "land status is not completely known")
    if evidence.chart_coverage_complete is not True:
        return RegionDecision("unresolved", "chart-aid catalog coverage is incomplete or unknown")
    if not detections:
        return RegionDecision(
            "survive", "no positive deterministic marker detections constrain the region"
        )

    objects = tuple(evidence.objects)
    if any(not isinstance(obj, RegionObject) for obj in objects):
        raise TypeError("objects must contain RegionObject values.")
    # Count pruning requires an explicit semantic requirement and proof that
    # every catalog row has a unique physical identity. Otherwise duplicates
    # and unresolved rows are retained as possible distinct markers.
    if minimum_distinct_markers is not None:
        keys = [obj.key for obj in objects]
        identities_complete = all(obj.identity_proven for obj in objects)
        identities_unique = len(set(keys)) == len(keys)
        positions_complete = all(
            obj.position_uncertainty_m is not None or obj.exact_position_asserted for obj in objects
        )
        if (
            identities_complete
            and identities_unique
            and positions_complete
            and evidence.certified_max_projection_scale is not None
            and _possible_object_count(
                cell, objects, visibility_radius_m, evidence.certified_max_projection_scale
            )
            < minimum_distinct_markers
        ):
            return RegionDecision(
                "prune", "complete chart catalog has too few proven distinct physical markers"
            )

    if certified_azimuth_error_deg is None or evidence.certified_max_projection_scale is None:
        return RegionDecision(
            "unresolved", "certified projected-to-WGS84 azimuth and scale bounds are required"
        )
    masks = _compatible_object_masks(
        cell,
        detections,
        objects,
        certified_azimuth_error_deg,
        visibility_radius_m,
        evidence.certified_max_projection_scale,
    )
    if any(not any(row) for row in masks):
        return RegionDecision("prune", "no catalog marker can match a positive detection")
    return RegionDecision("survive", "region retains compatible positive marker matches", masks)


def _validate_region_inputs(cells, detections, evidence, radius, minimum, azimuth_error):
    if not isfinite(radius) or radius < 0:
        raise ValueError("visibility radius must be finite and nonnegative.")
    if minimum is not None and (
        type(minimum) is not int or minimum < 1 or minimum > len(detections)
    ):
        raise ValueError(
            "minimum distinct markers must be positive and cannot exceed positive detections."
        )
    if azimuth_error is not None and (not isfinite(azimuth_error) or azimuth_error < 0):
        raise ValueError("certified azimuth error must be finite and nonnegative.")
    if any(not isinstance(cell, RegionCell) for cell in cells):
        raise TypeError("cells must contain RegionCell values.")
    if any(not isinstance(item, RegionEvidence) for item in evidence):
        raise TypeError("evidence must contain RegionEvidence values.")
    if any(not isinstance(detection, MarkerDetection) for detection in detections):
        raise TypeError("detections must contain MarkerDetection values.")
    for item in evidence:
        if item.chart_coverage_complete not in (True, False, None):
            raise TypeError("chart_coverage_complete must be bool or None.")
        if item.land_fully_covers not in (True, False, None):
            raise TypeError("land_fully_covers must be bool or None.")
        if type(item.map_evidence_covers_visibility_envelope) is not bool:
            raise TypeError("map evidence coverage flag must be bool.")
        if type(item.projection_reliable) is not bool or type(item.numeric_uncertainty) is not bool:
            raise TypeError("projection and numeric evidence flags must be bool.")
        scale = item.certified_max_projection_scale
        if scale is not None and (not isfinite(scale) or scale < 1.0):
            raise ValueError("certified maximum projection scale must be finite and at least one.")
        if any(not isinstance(obj, RegionObject) for obj in item.objects):
            raise TypeError("objects must contain RegionObject values.")


def _compatible_object_masks(cell, detections, objects, azimuth_error, radius, scale):
    if scale is None:
        return tuple(tuple(True for _ in objects) for _ in detections)
    x0, x1, y0, y1 = cell.bounds_en_m
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cell_radius = cell.covering_radius_m
    limit = radius * scale + 1e-6 + 1e-12 * radius * scale
    rows = []
    for detection in detections:
        row = []
        for obj in objects:
            uncertainty = obj.position_uncertainty_m
            if uncertainty is None and not obj.exact_position_asserted:
                row.append(True)
                continue
            uncertainty = uncertainty or 0.0
            dx, dy = obj.east_m - cx, obj.north_m - cy
            distance = hypot(dx, dy)
            if (
                max(x0 - obj.east_m, 0.0, obj.east_m - x1) ** 2
                + max(y0 - obj.north_m, 0.0, obj.north_m - y1) ** 2
                > (limit + uncertainty * scale) ** 2
            ):
                row.append(False)
                continue
            angular_radius = cell_radius + uncertainty * scale
            if distance <= angular_radius:
                row.append(True)
                continue
            half_angle = np.degrees(np.arcsin(min(1.0, angular_radius / distance)))
            predicted = np.degrees(np.arctan2(dx, dy)) % 360.0
            delta = abs((predicted - detection.bearing_deg + 180.0) % 360.0 - 180.0)
            row.append(delta <= detection.tolerance_deg + azimuth_error + half_angle + 1e-9)
        rows.append(tuple(row))
    return tuple(rows)


def _possible_object_count(cell, objects, radius_m, maximum_scale):
    x0, x1, y0, y1 = cell.bounds_en_m
    limit = radius_m * maximum_scale + 1e-6 + 1e-12 * radius_m * maximum_scale
    count = 0
    for obj in objects:
        if obj.position_uncertainty_m is None and not obj.exact_position_asserted:
            count += 1
            continue
        distance = hypot(
            max(x0 - obj.east_m, 0.0, obj.east_m - x1), max(y0 - obj.north_m, 0.0, obj.north_m - y1)
        )
        uncertainty = obj.position_uncertainty_m or 0.0
        count += distance <= limit + uncertainty * maximum_scale
    return count


def classify_regions_cupy(
    cells: Sequence[RegionCell],
    detections: Sequence[MarkerDetection],
    evidence: Sequence[RegionEvidence],
    *,
    visibility_radius_m: float,
    minimum_distinct_markers: int | None = None,
    certified_azimuth_error_deg: float | None = None,
    cupy_module=None,
    device_id: int = 0,
) -> tuple[RegionDecision, ...]:
    """Batch cell/object range bounds and safe count decisions on a GPU."""
    _validate_region_inputs(
        cells,
        detections,
        evidence,
        visibility_radius_m,
        minimum_distinct_markers,
        certified_azimuth_error_deg,
    )
    if len(cells) != len(evidence):
        raise ValueError("cells and evidence must have equal length.")
    if minimum_distinct_markers is not None and (
        type(minimum_distinct_markers) is not int
        or minimum_distinct_markers < 1
        or minimum_distinct_markers > len(detections)
    ):
        raise ValueError(
            "minimum distinct markers must be positive and cannot exceed positive detections."
        )
    if cupy_module is None:
        from importlib import import_module

        try:
            cupy_module = import_module("cupy")
        except ImportError:
            return tuple(
                classify_region(
                    c,
                    detections,
                    e,
                    visibility_radius_m=visibility_radius_m,
                    minimum_distinct_markers=minimum_distinct_markers,
                    certified_azimuth_error_deg=certified_azimuth_error_deg,
                )
                for c, e in zip(cells, evidence, strict=True)
            )
    cp = cupy_module
    if type(device_id) is not int or device_id < 0:
        raise ValueError("device_id must be a nonnegative integer.")
    if not cells:
        return ()
    max_objects = max((len(e.objects) for e in evidence), default=0)
    bounds = np.asarray([c.bounds_en_m for c in cells], dtype=np.float64)
    object_xy = np.zeros((len(cells), max_objects, 2), dtype=np.float64)
    uncertainty = np.zeros((len(cells), max_objects), dtype=np.float64)
    eligible = np.zeros((len(cells), max_objects), dtype=bool)
    positions_complete = np.ones(len(cells), dtype=bool)
    for i, item in enumerate(evidence):
        for j, obj in enumerate(item.objects):
            object_xy[i, j] = (obj.east_m, obj.north_m)
            object_scale = item.certified_max_projection_scale
            if object_scale is None:
                uncertainty[i, j] = np.inf
            else:
                uncertainty[i, j] = (
                    obj.position_uncertainty_m * object_scale
                    if obj.position_uncertainty_m is not None
                    else (0.0 if obj.exact_position_asserted else np.inf)
                )
            if obj.position_uncertainty_m is None and not obj.exact_position_asserted:
                positions_complete[i] = False
            eligible[i, j] = True
    with cp.cuda.Device(device_id):
        b, xy, u, valid = (
            cp.asarray(bounds),
            cp.asarray(object_xy),
            cp.asarray(uncertainty),
            cp.asarray(eligible),
        )
        dx = cp.maximum(cp.maximum(b[:, 0, None] - xy[:, :, 0], 0.0), xy[:, :, 0] - b[:, 1, None])
        dy = cp.maximum(cp.maximum(b[:, 2, None] - xy[:, :, 1], 0.0), xy[:, :, 1] - b[:, 3, None])
        lower = cp.maximum(cp.hypot(dx, dy) - u, 0.0)
        scales = cp.asarray(
            [
                e.certified_max_projection_scale
                if e.certified_max_projection_scale is not None
                else 1.0
                for e in evidence
            ]
        )
        scale_valid = cp.asarray([e.certified_max_projection_scale is not None for e in evidence])
        limit = visibility_radius_m * scales + 1e-6 + 1e-12 * visibility_radius_m * scales
        possible_counts = cp.asnumpy(cp.sum(valid & (lower <= limit[:, None]), axis=1))
        has_scale = cp.asnumpy(scale_valid)
        has_position_bounds = positions_complete
        host_lower = cp.asnumpy(lower)
        host_limit = cp.asnumpy(limit)
    masks_by_cell = [None] * len(cells)
    if certified_azimuth_error_deg is not None and detections:
        centers = np.asarray([c.center_xy_m for c in cells], dtype=np.float64)
        cell_radii = np.asarray([c.covering_radius_m for c in cells], dtype=np.float64)
        bearings = np.asarray([d.bearing_deg for d in detections], dtype=np.float64)
        tolerances = np.asarray([d.tolerance_deg for d in detections], dtype=np.float64)
        object_xy = np.asarray(object_xy)
        uncertainty = np.asarray(uncertainty)
        with cp.cuda.Device(device_id):
            cxy = cp.asarray(centers)[:, None, :]
            delta_xy = cp.asarray(object_xy) - cxy
            distance = cp.hypot(delta_xy[:, :, 0], delta_xy[:, :, 1])
            angular_radius = cp.asarray(cell_radii)[:, None] + cp.where(
                cp.isfinite(cp.asarray(uncertainty)), cp.asarray(uncertainty), 0.0
            )
            half_angle = cp.degrees(
                cp.arcsin(cp.minimum(1.0, angular_radius / cp.maximum(distance, 1e-300)))
            )
            predicted = cp.degrees(cp.arctan2(delta_xy[:, :, 0], delta_xy[:, :, 1])) % 360.0
            bearing_delta = cp.abs(
                (predicted[None, :, :] - cp.asarray(bearings)[:, None, None] + 180.0) % 360.0
                - 180.0
            )
            angular_ok = (distance[None, :, :] <= angular_radius[None, :, :]) | (
                bearing_delta
                <= cp.asarray(tolerances)[:, None, None]
                + certified_azimuth_error_deg
                + half_angle[None, :, :]
                + 1e-9
            )
            unknown_uncertainty = cp.asarray(~np.isfinite(uncertainty))
            angular_ok = angular_ok | unknown_uncertainty[None, :, :]
            angular_ok = angular_ok | (~cp.asarray(scale_valid))[None, :, None]
            range_possible = (~cp.asarray(scale_valid)[None, :, None]) | (
                cp.asarray(host_lower)[None, :, :] <= cp.asarray(host_limit)[None, :, None]
            )
            possible = angular_ok & cp.asarray(valid)[None, :, :] & range_possible
            host_possible = cp.asnumpy(possible)
        for i in range(len(cells)):
            object_count = len(evidence[i].objects)
            masks_by_cell[i] = tuple(
                tuple(bool(v) for v in row[:object_count]) for row in host_possible[:, i, :]
            )

    result = []
    for i, (cell, item) in enumerate(zip(cells, evidence, strict=True)):
        if not item.projection_reliable or item.numeric_uncertainty:
            decision = RegionDecision("unresolved", "projection or numeric evidence is incomplete")
        elif item.land_fully_covers is True:
            decision = RegionDecision(
                "prune", "reliable known land covers the entire observer cell"
            )
        elif not item.map_evidence_covers_visibility_envelope:
            decision = RegionDecision(
                "unresolved", "map evidence does not cover the full visibility envelope"
            )
        elif item.land_fully_covers is None:
            decision = RegionDecision("unresolved", "land status is not completely known")
        elif item.chart_coverage_complete is not True:
            decision = RegionDecision(
                "unresolved", "chart-aid catalog coverage is incomplete or unknown"
            )
        elif not detections:
            decision = RegionDecision(
                "survive", "no positive deterministic marker detections constrain the region"
            )
        elif (
            minimum_distinct_markers is not None
            and has_position_bounds[i]
            and has_scale[i]
            and possible_counts[i] < minimum_distinct_markers
        ):
            decision = RegionDecision(
                "prune", "complete chart catalog has too few proven distinct physical markers"
            )
        elif certified_azimuth_error_deg is None or not has_scale[i]:
            decision = RegionDecision(
                "unresolved", "certified projected-to-WGS84 azimuth and scale bounds are required"
            )
        else:
            masks = masks_by_cell[i]
            assert masks is not None
            if any(not any(row) for row in masks):
                decision = RegionDecision(
                    "prune", "no catalog marker can match a positive detection"
                )
            else:
                decision = RegionDecision(
                    "survive", "region retains compatible positive marker matches", masks
                )
        result.append(decision)
    return tuple(result)


__all__ = [
    "MarkerDetection",
    "RegionCell",
    "RegionDecision",
    "RegionEvidence",
    "RegionObject",
    "RegionStatus",
    "classify_region",
    "classify_regions_cupy",
]
