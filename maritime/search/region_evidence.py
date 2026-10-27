"""Build conservative evidence records for region pruning.

Chart-aid completeness is an independent caller assertion and polygon. It is
never inferred from shoreline coverage. The module deliberately keeps rows
with unknown types and duplicate keys because they remain possible objects.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from pyproj import Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_pruning import RegionCell, RegionEvidence, RegionObject


def build_region_evidence(
    vector_map: VectorMap,
    cell: RegionCell,
    *,
    visibility_radius_m: float,
    chart_coverage_geometry: BaseGeometry | None = None,
    chart_catalog_complete: bool = False,
    objects: Iterable[RegionObject] = (),
    projection_reliable: bool = True,
    numeric_uncertainty: bool = False,
    certified_max_projection_scale: float | None = None,
    catalog_objects_cover_envelope: bool = False,
) -> RegionEvidence:
    """Create pruning evidence from explicit map, chart, and projection inputs.

    ``chart_coverage_geometry`` is a projected polygon in the VectorMap CRS
    whose complete-catalog scope the caller has certified. A true
    ``chart_catalog_complete`` and ``catalog_objects_cover_envelope`` must both
    be true, and the polygon must cover the full certified cell visibility
    envelope. Catalog objects must already be conservatively projected into
    the map CRS; unknown type rows should be kept in ``objects``. No type-based
    exclusion is performed here.

    Projection scale is a caller certificate over the complete envelope; an
    absent or invalid certificate is retained as unknown. `VectorMap` land
    semantics treat boundaries as water, so a cell is certified land only when
    it is strictly contained in known land and no cell boundary touches land.
    """
    if not isinstance(vector_map, VectorMap) or not isinstance(cell, RegionCell):
        raise TypeError("vector_map and cell must use their declared contracts.")
    if not isfinite(visibility_radius_m) or visibility_radius_m < 0:
        raise ValueError("visibility_radius_m must be finite and nonnegative.")
    if certified_max_projection_scale is not None and (
        not isfinite(certified_max_projection_scale) or certified_max_projection_scale < 1.0
    ):
        raise ValueError("certified projection scale must be finite and at least one.")
    rows = tuple(objects)
    if any(not isinstance(obj, RegionObject) for obj in rows):
        raise TypeError("objects must contain RegionObject values.")
    region = _cell_polygon(cell.bounds_en_m)
    scale_certified = certified_max_projection_scale is not None
    envelope = None
    if scale_certified:
        scaled_radius = visibility_radius_m * certified_max_projection_scale
        margin = 1e-6 + 1e-12 * scaled_radius
        envelope = _cell_polygon(cell.visibility_envelope(scaled_radius + margin))

    map_coverage = getattr(vector_map, "_coverage", None)
    map_unknown = getattr(vector_map, "_unknown", None)
    map_covers_cell = bool(
        map_coverage is not None
        and map_coverage.covers(region)
        and (map_unknown is None or map_unknown.disjoint(region))
    )
    map_covers_envelope = bool(
        scale_certified
        and envelope is not None
        and map_coverage is not None
        and map_coverage.covers(envelope)
        and (map_unknown is None or map_unknown.disjoint(envelope))
    )

    land = vector_map.land_geometry
    land_fully_covers = bool(
        map_covers_cell and land.contains(region) and land.boundary.disjoint(region)
    )
    if map_unknown is not None and map_unknown.intersects(region):
        land_status: bool | None = None
    elif not map_covers_cell:
        land_status = None
    else:
        # Complete known map evidence over the observer cell distinguishes
        # non-land from unknown and permits marker pruning on known water.
        land_status = land_fully_covers

    chart_complete = bool(
        chart_catalog_complete
        and catalog_objects_cover_envelope
        and scale_certified
        and envelope is not None
        and chart_coverage_geometry is not None
        and isinstance(chart_coverage_geometry, BaseGeometry)
        and not chart_coverage_geometry.is_empty
        and chart_coverage_geometry.is_valid
        and chart_coverage_geometry.covers(envelope)
    )
    return RegionEvidence(
        chart_coverage_complete=chart_complete,
        land_fully_covers=land_status,
        map_evidence_covers_visibility_envelope=map_covers_envelope,
        projection_reliable=bool(projection_reliable),
        numeric_uncertainty=bool(numeric_uncertainty),
        objects=rows,
        certified_max_projection_scale=certified_max_projection_scale,
    )


def _cell_polygon(bounds_en_m: tuple[float, float, float, float]):
    """Convert (xmin, xmax, ymin, ymax) into Shapely's box argument order."""
    x_min, x_max, y_min, y_max = bounds_en_m
    return box(x_min, y_min, x_max, y_max)


def project_catalog_objects(
    records: Iterable[object],
    *,
    target_crs: str | int,
    uncertainty_m: float | None = None,
    identity_proven: bool = False,
    exact_position_asserted: bool = False,
) -> tuple[RegionObject, ...]:
    """Project geographic catalog records into a projected metre CRS.

    This helper preserves every input row, including duplicate identities and
    unknown types. It does not infer chart completeness or identity proof.
    """
    if uncertainty_m is not None and (not isfinite(uncertainty_m) or uncertainty_m < 0):
        raise ValueError("uncertainty_m must be finite and nonnegative, or None.")
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    result = []
    for row_index, record in enumerate(records):
        if not all(hasattr(record, key) for key in ("key", "latitude_deg", "longitude_deg")):
            raise TypeError("catalog records must expose key, latitude_deg, and longitude_deg.")
        east, north = transformer.transform(
            getattr(record, "longitude_deg"), getattr(record, "latitude_deg")
        )
        if not isfinite(east) or not isfinite(north):
            raise ValueError("catalog coordinate projection produced a non-finite position.")
        # Keep duplicate keys distinct as rows. The downstream count code then
        # refuses unique-identity reasoning when duplicate IDs are present.
        original_key = getattr(record, "key")
        key = str(original_key) if original_key else f"unknown-row-{row_index}"
        result.append(
            RegionObject(
                key=key,
                east_m=east,
                north_m=north,
                position_uncertainty_m=uncertainty_m,
                identity_proven=identity_proven,
                exact_position_asserted=exact_position_asserted,
            )
        )
    return tuple(result)


__all__ = ["build_region_evidence", "project_catalog_objects"]
