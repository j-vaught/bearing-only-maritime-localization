"""End-to-end coastwise marker search with conservative region retention.

Each retained terminal region receives one labeled center sample. The sample
never removes or certifies the rest of its cell. A budget stop reports the full
scheduler frontier.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from math import atan2, degrees, hypot, isfinite
from pathlib import Path
from typing import Any

from pyproj import Geod, Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from maritime.catalog_identity import canonicalize_chart_aids
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2
from maritime.schemas.object_types import normalize_object_type
from maritime.search.deterministic_marker_scoring import MarkerObservation
from maritime.search.gpu.region_prune_pipeline import prune_map_regions_gpu
from maritime.search.gpu.region_pruning import MarkerDetection, RegionCell
from maritime.search.prediction import _visible_feature_offset, predict_visible_groups
from maritime.search.region_scheduler import RegionTraversalResult

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class CoastwiseCellScore:
    """A scorer result at the center sample of one retained terminal cell."""

    cell: RegionCell
    sample_position_xy_m: tuple[float, float]
    sample_semantics: str
    map_status: str
    point_water_status: str
    chart_complete: bool | None
    score: Any


@dataclass(frozen=True, slots=True)
class CoastwiseMarkerSearchResult:
    """Inspectable region traversal, terminal samples, and budget frontier."""

    status: str
    traversal: RegionTraversalResult
    center_scores: tuple[CoastwiseCellScore, ...]
    score_errors: tuple[tuple[tuple[float, float, float, float], str], ...]
    metadata: dict[str, Any]

    @property
    def complete(self) -> bool:
        return self.status == "complete"


def run_coastwise_marker_search(
    vector_map: VectorMap,
    catalog: CatalogV2,
    markers: Sequence[MarkerObservation],
    *,
    search_bounds_en_m: tuple[float, float, float, float],
    chart_coverage_geometry: BaseGeometry | None = None,
    chart_catalog_complete: bool = False,
    catalog_objects_cover_envelope: bool = False,
    catalog_object_identities_proven: bool = False,
    catalog_position_uncertainty_m: float | None = None,
    catalog_exact_positions_asserted: bool = False,
    catalog_nominal_visible_offsets_asserted: bool = False,
    minimum_distinct_markers: int | None = None,
    visibility_radius_m: float,
    certified_max_projection_scale: float | None = None,
    certified_azimuth_error_deg: float | None = None,
    device_ids: Sequence[int],
    matching_backend: str = "cpu",
    output_path: str | Path,
    initial_cell_size_m: float = 10_000.0,
    target_cell_size_m: float = 1_000.0,
    batch_size: int = 16,
    max_evaluations: int | None = None,
    enable_land_blocking: bool = True,
    certified_land_barriers_en_m: Sequence[tuple[float, float, float, float]] = (),
    progress: Callable[[str, dict[str, int]], None] | None = None,
    cupy_module: Any | None = None,
) -> CoastwiseMarkerSearchResult:
    """Prune and score all retained cells without top-N truncation.

    Completeness geometry and chart, catalog, projection, and azimuth
    certificates are explicit inputs. Missing evidence leaves cells unresolved.
    No marker absence or AIS nonmatch eliminates a candidate. The default
    initial grid is 10 km, with configurable refinement to terminal resolution.
    """
    if not isinstance(vector_map, VectorMap):
        raise TypeError("vector_map must be a VectorMap.")
    if not isinstance(catalog, CatalogV2):
        raise TypeError("catalog must be a CatalogV2.")
    catalog = canonicalize_chart_aids(catalog)
    if matching_backend not in ("cpu", "gpu"):
        raise ValueError("matching_backend must be cpu or gpu.")
    marker_rows = tuple(markers)
    if any(not isinstance(m, MarkerObservation) for m in marker_rows):
        raise TypeError("markers must contain MarkerObservation records.")

    detections = tuple(
        MarkerDetection(float(m.bearing_deg) % 360.0, float(m.tolerance_deg))
        for m in marker_rows
        if (
            m.positive
            and m.tolerance_calibrated
            and m.identity_status == "resolved"
            and m.tolerance_deg < 180.0
        )
    )
    if type(catalog_object_identities_proven) is not bool:
        raise TypeError("catalog_object_identities_proven must be bool.")
    if type(catalog_exact_positions_asserted) is not bool:
        raise TypeError("catalog_exact_positions_asserted must be bool.")
    if type(catalog_nominal_visible_offsets_asserted) is not bool:
        raise TypeError("catalog_nominal_visible_offsets_asserted must be bool.")
    if catalog_position_uncertainty_m is not None and (
        not isinstance(catalog_position_uncertainty_m, (int, float))
        or isinstance(catalog_position_uncertainty_m, bool)
        or not isfinite(catalog_position_uncertainty_m)
        or catalog_position_uncertainty_m < 0
    ):
        raise ValueError(
            "catalog_position_uncertainty_m must be a nonnegative meter bound or None."
        )
    if catalog_exact_positions_asserted and catalog_position_uncertainty_m not in (None, 0, 0.0):
        raise ValueError("exact positions cannot have a positive uncertainty bound.")
    trustworthy_positive_count = sum(
        m.positive and m.tolerance_calibrated and m.identity_status == "resolved"
        for m in marker_rows
    )
    if minimum_distinct_markers is not None and (
        type(minimum_distinct_markers) is not int
        or minimum_distinct_markers < 1
        or minimum_distinct_markers > trustworthy_positive_count
    ):
        raise ValueError(
            "minimum_distinct_markers must be within the trustworthy positive marker count."
        )
    region_objects = _catalog_region_objects(
        catalog,
        vector_map,
        position_uncertainty_m=catalog_position_uncertainty_m,
        identity_proven=catalog_object_identities_proven,
        exact_position_asserted=catalog_exact_positions_asserted,
        nominal_visible_offsets_asserted=catalog_nominal_visible_offsets_asserted,
    )

    def report(stage: str, values: dict[str, int]) -> None:
        if progress is not None:
            progress(stage, dict(values))

    report("region_pruning_started", {"initial_cell_size_m": int(initial_cell_size_m)})

    def traversal_progress(evaluated: int, frontier: int, pruned: int) -> None:
        report("region_pruning", {"evaluated": evaluated, "frontier": frontier, "pruned": pruned})

    traversal = prune_map_regions_gpu(
        vector_map,
        region_objects,
        detections,
        search_bounds_en_m=search_bounds_en_m,
        chart_coverage_geometry=chart_coverage_geometry,
        chart_catalog_complete=chart_catalog_complete,
        catalog_objects_cover_envelope=catalog_objects_cover_envelope,
        visibility_radius_m=visibility_radius_m,
        certified_max_projection_scale=certified_max_projection_scale,
        certified_azimuth_error_deg=certified_azimuth_error_deg,
        minimum_distinct_markers=minimum_distinct_markers,
        device_ids=device_ids,
        initial_cell_size_m=initial_cell_size_m,
        target_cell_size_m=target_cell_size_m,
        batch_size=batch_size,
        max_evaluations=max_evaluations,
        enable_land_blocking=enable_land_blocking,
        certified_land_barriers_en_m=certified_land_barriers_en_m,
        progress=traversal_progress,
        cupy_module=cupy_module,
    )
    report("center_scoring_started", {"retained_terminal_cells": len(traversal.retained)})

    from maritime.search.deterministic_marker_scoring import score_marker_candidates

    center_scores: list[CoastwiseCellScore] = []
    score_errors: list[tuple[tuple[float, float, float, float], str]] = []
    for index, retained in enumerate(traversal.retained, start=1):
        center = retained.cell.center_xy_m
        point_status = vector_map.point_water_status(center)
        map_status = {"water": "known", "land": "land", "unknown": "unknown"}[point_status]
        try:
            predicted = predict_visible_groups(
                center,
                catalog,
                observer_crs=vector_map.crs,
                visibility_radius_m=visibility_radius_m,
                vector_map=vector_map,
            )
            chart_complete = _center_chart_completeness(
                center,
                visibility_radius_m=visibility_radius_m,
                chart_coverage_geometry=chart_coverage_geometry,
                chart_catalog_complete=chart_catalog_complete,
                catalog_objects_cover_envelope=catalog_objects_cover_envelope,
                certified_max_projection_scale=certified_max_projection_scale,
            )
            score = score_marker_candidates(
                marker_rows,
                predicted,
                catalog,
                map_status=map_status,
                chart_complete=chart_complete,
                catalog_position_uncertainty_m=catalog_position_uncertainty_m,
                catalog_exact_positions_asserted=catalog_exact_positions_asserted,
                catalog_object_identities_proven=catalog_object_identities_proven,
                catalog_nominal_visible_offsets_asserted=catalog_nominal_visible_offsets_asserted,
                matching_backend=matching_backend,
                device_id=device_ids[(index - 1) % len(device_ids)]
                if matching_backend == "gpu"
                else None,
                cupy_module=cupy_module if matching_backend == "gpu" else None,
            )
            center_scores.append(
                CoastwiseCellScore(
                    retained.cell,
                    center,
                    "cell_center_only; no claim for other points in the cell",
                    map_status,
                    point_status,
                    chart_complete,
                    score,
                )
            )
        except Exception as exc:
            # A failed point sample leaves its complete terminal cell retained.
            score_errors.append((retained.cell.bounds_en_m, f"{type(exc).__name__}: {exc}"))
        if index % max(1, batch_size) == 0 or index == len(traversal.retained):
            report(
                "center_scoring", {"scored": len(center_scores), "total": len(traversal.retained)}
            )

    status = "complete" if traversal.complete else "budget_limited"
    metadata = {
        "schema": "CoastwiseMarkerSearchResultV1",
        "created_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "status_scope": "region traversal only; center scoring outcomes are separate",
        "domain_bounds_en_m": list(search_bounds_en_m),
        "initial_cell_size_m": initial_cell_size_m,
        "target_cell_size_m": target_cell_size_m,
        "visibility_radius_m": visibility_radius_m,
        "chart_catalog_complete_asserted": chart_catalog_complete,
        "catalog_objects_cover_envelope_asserted": catalog_objects_cover_envelope,
        "chart_coverage_geometry_supplied": chart_coverage_geometry is not None,
        "certified_max_projection_scale": certified_max_projection_scale,
        "certified_azimuth_error_deg": certified_azimuth_error_deg,
        "positive_marker_count": sum(marker.positive for marker in marker_rows),
        "pruning_detection_count": len(detections),
        "catalog_record_count": len(catalog.records),
        "catalog_id": catalog.catalog_id,
        "catalog_version": catalog.catalog_version,
        "catalog_timestamp_utc": catalog.timestamp_utc,
        "catalog_source": catalog.source,
        "catalog_object_identities_proven_asserted": catalog_object_identities_proven,
        "catalog_position_uncertainty_m_asserted": catalog_position_uncertainty_m,
        "catalog_exact_positions_asserted": catalog_exact_positions_asserted,
        "catalog_nominal_visible_offsets_asserted": catalog_nominal_visible_offsets_asserted,
        "minimum_distinct_markers_asserted": minimum_distinct_markers,
        "marker_observations": [
            {
                "marker_id": marker.marker_id,
                "bearing_deg": marker.bearing_deg,
                "tolerance_deg": marker.tolerance_deg,
                "positive": marker.positive,
                "identity_status": marker.identity_status,
                "tolerance_calibrated": marker.tolerance_calibrated,
            }
            for marker in marker_rows
        ],
        "map_crs": vector_map.crs.to_string(),
        "chart_coverage_geometry_sha256": (
            hashlib.sha256(chart_coverage_geometry.wkb).hexdigest()
            if chart_coverage_geometry is not None
            else None
        ),
        "chart_coverage_geometry_bounds": (
            list(chart_coverage_geometry.bounds) if chart_coverage_geometry is not None else None
        ),
        "device_ids": list(device_ids),
        "matching_backend": matching_backend,
        "traversal_evaluated_count": traversal.evaluated_count,
        "traversal_pruned_count": traversal.pruned_count,
        "retained_terminal_count": len(traversal.retained),
        "budget_frontier_count": len(traversal.unprocessed),
        "center_score_count": len(center_scores),
        "center_score_error_count": len(score_errors),
        "score_semantics": "each score applies only to the listed cell-center sample",
        "completeness_semantics": (
            "finite domain traversed; retained cells remain spatial alternatives"
            if traversal.complete
            else "budget ended; complete unprocessed frontier is included"
        ),
        "interrupt_semantics": "KeyboardInterrupt frontier is not currently checkpointed",
    }
    result = CoastwiseMarkerSearchResult(
        status, traversal, tuple(center_scores), tuple(score_errors), metadata
    )
    _write_json_atomic(Path(output_path), _result_payload(result))
    report(
        "finished",
        {"complete": int(result.complete), "budget_frontier": len(traversal.unprocessed)},
    )
    return result


def _center_chart_completeness(
    center: tuple[float, float],
    *,
    visibility_radius_m: float,
    chart_coverage_geometry: BaseGeometry | None,
    chart_catalog_complete: bool,
    catalog_objects_cover_envelope: bool,
    certified_max_projection_scale: float | None,
) -> bool | None:
    """Certify chart scope only when the whole conservative center envelope is covered."""
    if not chart_catalog_complete or not catalog_objects_cover_envelope:
        return False
    if (
        chart_coverage_geometry is None
        or chart_coverage_geometry.is_empty
        or not chart_coverage_geometry.is_valid
        or certified_max_projection_scale is None
        or not isfinite(certified_max_projection_scale)
        or certified_max_projection_scale < 1.0
    ):
        return None
    radius = visibility_radius_m * certified_max_projection_scale
    radius += 1e-6 + 1e-12 * radius
    x, y = center
    envelope = box(x - radius, y - radius, x + radius, y + radius)
    return bool(chart_coverage_geometry.covers(envelope))


def _catalog_region_objects(
    catalog: CatalogV2,
    vector_map: VectorMap,
    *,
    position_uncertainty_m: float | None,
    identity_proven: bool,
    exact_position_asserted: bool,
    nominal_visible_offsets_asserted: bool = False,
):
    """Project possible chart objects, respecting explicit offset and position bounds."""
    from math import isfinite

    from maritime.search.deterministic_marker_scoring import _effective_position_uncertainty
    from maritime.search.gpu.region_pruning import RegionObject

    transformer = Transformer.from_crs("EPSG:4326", vector_map.crs, always_xy=True)
    objects = []
    catalog = canonicalize_chart_aids(catalog)
    for record in catalog.records:
        object_type = normalize_object_type(record.normalized_type or record.raw_type)
        if object_type in {"vessel", "virtual_aid"}:
            continue
        east_offset, north_offset, offset_quality = _visible_feature_offset(record)
        offset_is_certified = offset_quality == "calibrated" or (
            offset_quality == "nominal_zero_unknown" and nominal_visible_offsets_asserted
        )
        if offset_is_certified and (east_offset != 0.0 or north_offset != 0.0):
            offset_bearing = degrees(atan2(east_offset, north_offset))
            offset_range = hypot(east_offset, north_offset)
            longitude, latitude, _ = _GEOD.fwd(
                record.longitude_deg, record.latitude_deg, offset_bearing, offset_range
            )
        else:
            longitude, latitude = record.longitude_deg, record.latitude_deg
        east, north = transformer.transform(longitude, latitude)
        if not isfinite(east) or not isfinite(north):
            raise ValueError(f"Catalog row {record.key!r} has a non-finite projected position.")
        effective_uncertainty = _effective_position_uncertainty(
            record, bound_m=position_uncertainty_m, exact_asserted=exact_position_asserted
        )
        objects.append(
            RegionObject(
                key=record.key,
                east_m=float(east),
                north_m=float(north),
                position_uncertainty_m=(effective_uncertainty if offset_is_certified else None),
                identity_proven=identity_proven,
                exact_position_asserted=(offset_is_certified and effective_uncertainty == 0.0),
            )
        )
    return tuple(objects)


def _result_payload(result: CoastwiseMarkerSearchResult) -> dict[str, Any]:
    return {
        "metadata": result.metadata,
        "traversal": {
            "evaluated_count": result.traversal.evaluated_count,
            "pruned_count": result.traversal.pruned_count,
            "device_evaluations": [list(row) for row in result.traversal.device_evaluations],
            "prune_reasons": [list(row) for row in result.traversal.prune_reasons],
            "retained": [
                {
                    "bounds_en_m": list(item.cell.bounds_en_m),
                    "level": item.cell.level,
                    "status": item.decision.status,
                    "reason": item.decision.reason,
                }
                for item in result.traversal.retained
            ],
            "unprocessed_frontier": [
                {"bounds_en_m": list(cell.bounds_en_m), "level": cell.level}
                for cell in result.traversal.unprocessed
            ],
        },
        "center_scores": [
            {
                "bounds_en_m": list(item.cell.bounds_en_m),
                "level": item.cell.level,
                "sample_position_xy_m": list(item.sample_position_xy_m),
                "sample_semantics": item.sample_semantics,
                "point_water_status": item.point_water_status,
                "map_status": item.map_status,
                "chart_complete": item.chart_complete,
                "score": _jsonable(item.score),
            }
            for item in result.center_scores
        ],
        "center_score_errors": [
            {"bounds_en_m": list(bounds), "error": error} for bounds, error in result.score_errors
        ],
    }


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return _jsonable(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


__all__ = ["CoastwiseCellScore", "CoastwiseMarkerSearchResult", "run_coastwise_marker_search"]
