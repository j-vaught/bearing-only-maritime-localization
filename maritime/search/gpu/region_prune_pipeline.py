"""Connect conservative map evidence and GPU pruning to the region queue."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Any

from shapely.geometry.base import BaseGeometry

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_pruning import (
    MarkerDetection,
    RegionCell,
    RegionDecision,
    RegionObject,
    classify_regions_cupy,
)
from maritime.search.region_evidence import build_region_evidence
from maritime.search.region_scheduler import (
    RegionTraversalResult,
    make_initial_regions,
    traverse_regions,
)


def prune_map_regions_gpu(
    vector_map: VectorMap,
    objects: Sequence[RegionObject],
    marker_detections: Sequence[MarkerDetection],
    *,
    search_bounds_en_m: tuple[float, float, float, float],
    chart_coverage_geometry: BaseGeometry | None,
    chart_catalog_complete: bool,
    catalog_objects_cover_envelope: bool,
    visibility_radius_m: float,
    certified_max_projection_scale: float | None,
    certified_azimuth_error_deg: float | None,
    minimum_distinct_markers: int | None,
    device_ids: Sequence[int],
    initial_cell_size_m: float = 10_000.0,
    target_cell_size_m: float = 1_000.0,
    batch_size: int = 16,
    max_evaluations: int | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    cupy_module: Any | None = None,
) -> RegionTraversalResult:
    """Prune every region using positive marker evidence and preserve unresolved cells.

    Chart completeness, object scope and projection/error bounds are explicit
    caller certificates; no chart completeness is inferred from shoreline data.
    Every valid survivor is subdivided until the requested size. No region is
    ranked away. CPU geometry builds evidence, while region/object range and
    bearing compatibility are calculated in batches on the named CUDA devices.
    No vessel absence or unmatched chart marker penalty is applied.
    """
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    initial = make_initial_regions(search_bounds_en_m, cell_size_m=initial_cell_size_m)
    all_objects = tuple(objects)
    detections = tuple(marker_detections)

    def evaluate(device_id: int, cells: tuple[RegionCell, ...]) -> tuple[RegionDecision, ...]:
        evidence = tuple(
            build_region_evidence(
                vector_map,
                cell,
                visibility_radius_m=visibility_radius_m,
                chart_coverage_geometry=chart_coverage_geometry,
                chart_catalog_complete=chart_catalog_complete,
                catalog_objects_cover_envelope=catalog_objects_cover_envelope,
                certified_max_projection_scale=certified_max_projection_scale,
                objects=all_objects,
            )
            for cell in cells
        )
        return classify_regions_cupy(
            cells,
            detections,
            evidence,
            visibility_radius_m=visibility_radius_m,
            minimum_distinct_markers=minimum_distinct_markers,
            certified_azimuth_error_deg=certified_azimuth_error_deg,
            cupy_module=cp,
            device_id=device_id,
        )

    return traverse_regions(
        initial,
        evaluate,
        device_ids=device_ids,
        target_cell_size_m=target_cell_size_m,
        batch_size=batch_size,
        max_evaluations=max_evaluations,
        progress=progress,
    )


__all__ = ["prune_map_regions_gpu"]
