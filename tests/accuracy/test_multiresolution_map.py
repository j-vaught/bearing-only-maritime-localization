"""Accuracy checks for conservative multiresolution map summaries."""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon, box

from maritime.geometry.multires_map import MultiResolutionMap
from maritime.geometry.vector_map import VectorMap

CRS_METRES = "EPSG:32618"


def make_map(
    land: Polygon,
    *,
    coverage: Polygon | None = None,
    unknown: Polygon | None = None,
) -> VectorMap:
    return VectorMap(
        land,
        crs=CRS_METRES,
        coverage_geometry=coverage or box(0.0, 0.0, 12.0, 12.0),
        unknown_geometry=unknown,
    )


def test_subcell_island_remains_uncertain_until_finer_resolution() -> None:
    vector_map = make_map(box(4.2, 4.2, 4.8, 4.8))
    summaries = MultiResolutionMap(
        vector_map,
        bounds_m=(0.0, 0.0, 12.0, 12.0),
        resolutions_m=(4.0, 1.0, 0.25),
    )

    coarse = summaries.classify((4.5, 4.5), resolution_m=4.0, exact_check=True)
    medium = summaries.classify((4.5, 4.5), resolution_m=1.0, exact_check=True)
    fine = summaries.classify((4.5, 4.5), resolution_m=0.25, exact_check=True)

    assert coarse.status == "uncertain"
    assert medium.status == "uncertain"
    assert fine.status == "definitely_land"
    assert coarse.exact_status == medium.exact_status == fine.exact_status == "land"
    assert summaries.classify((5.5, 4.5), resolution_m=4.0).status == "uncertain"


def test_narrow_inlet_and_cell_boundary_gap_are_never_coarse_blocked() -> None:
    # The hole is a narrow inlet through an otherwise large land polygon.
    peninsula = (
        box(2.0, 2.0, 5.8, 10.0).union(box(6.2, 2.0, 10.0, 10.0)).union(box(5.8, 8.0, 6.2, 10.0))
    )
    summaries = MultiResolutionMap(
        make_map(peninsula),
        bounds_m=(0.0, 0.0, 12.0, 12.0),
        resolutions_m=(4.0, 1.0, 0.1),
    )

    coarse_inlet = summaries.classify((6.0, 4.0), resolution_m=4.0, exact_check=True)
    fine_inlet = summaries.classify((6.05, 4.0), resolution_m=0.1, exact_check=True)
    broad_land = summaries.classify((3.0, 4.0), resolution_m=4.0)

    assert coarse_inlet.status == "uncertain"
    assert coarse_inlet.exact_status == "water"
    assert fine_inlet.status == "definitely_water"
    assert broad_land.status == "uncertain"


def test_coverage_seams_and_unknown_regions_are_uncertain() -> None:
    coverage = box(0.0, 0.0, 5.0, 10.0).union(box(5.5, 0.0, 10.0, 10.0))
    unknown = box(7.0, 0.0, 7.5, 10.0)
    vector_map = make_map(box(1.0, 1.0, 3.0, 9.0), coverage=coverage, unknown=unknown)
    summaries = MultiResolutionMap(
        vector_map,
        bounds_m=(0.0, 0.0, 10.0, 10.0),
        resolutions_m=(2.0, 0.25),
    )

    assert summaries.classify((5.25, 4.0), resolution_m=2.0).status == "uncertain"
    assert summaries.classify((7.25, 4.0), resolution_m=2.0).status == "uncertain"
    assert summaries.classify((4.25, 4.0), resolution_m=0.25).status == "definitely_water"
    assert summaries.classify((2.25, 4.0), resolution_m=0.25).status == "definitely_land"
    assert summaries.classify((10.0, 4.0), resolution_m=0.25).status == "uncertain"


def test_overlapping_land_features_and_polygon_holes_are_classified_exactly() -> None:
    island = box(1.0, 1.0, 5.0, 5.0).difference(box(2.0, 2.0, 4.0, 4.0))
    vector_map = VectorMap(
        [box(1.0, 1.0, 5.0, 5.0), box(4.0, 1.0, 6.0, 5.0)],
        crs=CRS_METRES,
        coverage_geometry=box(0.0, 0.0, 8.0, 8.0),
    )
    # Use a separate map to preserve the explicit polygon hole while also
    # checking overlap normalization in the constructor above.
    assert vector_map.point_water_status((4.5, 3.0)) == "land"
    overlap_summaries = MultiResolutionMap(
        vector_map,
        bounds_m=(0.0, 0.0, 8.0, 8.0),
        resolutions_m=(1.0, 0.25),
    )
    assert overlap_summaries.classify((4.125, 3.0), resolution_m=0.25).status == "definitely_land"
    holed_map = make_map(island)
    summaries = MultiResolutionMap(
        holed_map,
        bounds_m=(0.0, 0.0, 8.0, 8.0),
        resolutions_m=(2.0, 0.5),
    )

    assert summaries.classify((1.5, 1.5), resolution_m=0.5).status == "definitely_land"
    assert summaries.classify((3.0, 3.0), resolution_m=0.5).status == "definitely_water"
    assert summaries.classify((4.5, 3.0), resolution_m=2.0).status == "uncertain"


def test_dense_brute_point_queries_find_no_false_definitive_cells() -> None:
    land = Polygon(
        [(2.1, 1.0), (7.0, 1.0), (7.0, 7.0), (2.1, 7.0)],
        holes=[[(4.1, 2.2), (5.4, 2.2), (5.4, 6.5), (4.1, 6.5)]],
    ).union(box(8.12, 1.1, 8.18, 6.9))
    coverage = box(0.0, 0.0, 9.0, 9.0).difference(box(0.0, 8.6, 9.0, 9.0))
    vector_map = make_map(land, coverage=coverage, unknown=box(0.5, 0.5, 0.7, 8.0))
    summaries = MultiResolutionMap(
        vector_map,
        bounds_m=(0.0, 0.0, 9.0, 9.0),
        resolutions_m=(3.0, 1.0, 0.25),
    )
    x = np.linspace(0.001, 8.999, 181)
    y = np.linspace(0.001, 8.999, 181)
    points = np.array([(px, py) for py in y for px in x])

    diagnostics = {
        resolution: summaries.compare_points(points, resolution_m=resolution)
        for resolution in summaries.resolutions_m
    }

    assert all(counts["checked"] == len(points) for counts in diagnostics.values())
    assert all(counts["disagreements"] == 0 for counts in diagnostics.values()), diagnostics
    assert diagnostics[3.0]["definitive"] == 0, diagnostics
    assert diagnostics[1.0]["definitive"] > 0, diagnostics
    assert diagnostics[0.25]["definitive"] > diagnostics[1.0]["definitive"], diagnostics


def test_constructor_and_query_validate_bounds_and_resolution() -> None:
    vector_map = make_map(box(1.0, 1.0, 2.0, 2.0))
    summaries = MultiResolutionMap(
        vector_map,
        bounds_m=(0.0, 0.0, 4.0, 4.0),
        resolutions_m=(1.0, 0.5),
    )

    try:
        MultiResolutionMap(vector_map, bounds_m=(0.0, 0.0, 0.0, 1.0), resolutions_m=(1.0,))
    except ValueError:
        pass
    else:
        raise AssertionError("degenerate bounds must be rejected")
    try:
        summaries.classify((1.0, 1.0), resolution_m=0.75)
    except ValueError:
        pass
    else:
        raise AssertionError("unconfigured resolutions must be rejected")
