"""Connect conservative map evidence and GPU pruning to the region queue."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Any

from shapely.geometry.base import BaseGeometry

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_land_visibility import classify_land_blocking_cupy
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
    enable_land_blocking: bool = True,
    certified_land_barriers_en_m: Sequence[tuple[float, float, float, float]] = (),
    progress: Callable[[int, int, int], None] | None = None,
    cupy_module: Any | None = None,
) -> RegionTraversalResult:
    """Prune every region using positive marker evidence and preserve unresolved cells.

    Chart completeness, object scope and projection/error bounds are explicit
    caller certificates; no chart completeness is inferred from shoreline data.
    Every valid survivor is subdivided until the requested size. No region is
    ranked away. CPU geometry builds evidence, while region/object range and
    bearing compatibility are calculated in batches on the named CUDA devices.
    No vessel absence or unmatched chart marker penalty is applied. Optional
    land blocking rejects only proven universal occlusion by known land; lack
    of a proof never excludes a region.
    """
    if type(enable_land_blocking) is not bool:
        raise TypeError("enable_land_blocking must be bool.")
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
        decisions = list(
            classify_regions_cupy(
                cells,
                detections,
                evidence,
                visibility_radius_m=visibility_radius_m,
                minimum_distinct_markers=minimum_distinct_markers,
                certified_azimuth_error_deg=certified_azimuth_error_deg,
                cupy_module=cp,
                device_id=device_id,
            )
        )
        eligible = [i for i, decision in enumerate(decisions) if decision.status != "prune"]
        if enable_land_blocking and detections and eligible:
            masks = []
            for i in eligible:
                recorded = decisions[i].compatible_object_masks
                masks.append(
                    recorded
                    if recorded
                    else tuple(tuple(True for _ in evidence[i].objects) for _ in detections)
                )
            land = classify_land_blocking_cupy(
                [cells[i] for i in eligible],
                detections,
                [evidence[i] for i in eligible],
                vector_map,
                compatible_object_masks=masks,
                certified_land_barriers_en_m=certified_land_barriers_en_m,
                cupy_module=cp,
                device_id=device_id,
            )
            for index, proof in zip(eligible, land, strict=True):
                if proof.blocked:
                    decisions[index] = RegionDecision("prune", proof.reason)
        return tuple(decisions)

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
