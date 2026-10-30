from shapely.geometry import GeometryCollection, LineString, box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2
from maritime.search.gpu.region_pruning import RegionCell
from maritime.search.region_evidence import build_region_evidence, project_catalog_objects


def test_chart_completeness_is_separate_from_land_coverage():
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=box(0, 0, 1000, 1000))
    cell = RegionCell((100, 300, 400, 500))
    result = build_region_evidence(
        vm,
        cell,
        visibility_radius_m=50,
        chart_coverage_geometry=box(40, 340, 360, 560),
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=True,
        certified_max_projection_scale=1.1,
    )
    assert result.map_evidence_covers_visibility_envelope
    assert result.land_fully_covers is False
    assert result.chart_coverage_complete


def test_full_cell_land_proof_requires_strict_interior():
    coverage = box(0, 0, 1000, 1000)
    vm = VectorMap(box(0, 0, 300, 700), crs="EPSG:32618", coverage_geometry=coverage)
    inside = build_region_evidence(vm, RegionCell((100, 200, 400, 500)), visibility_radius_m=10)
    boundary_touch = build_region_evidence(
        vm, RegionCell((300, 400, 400, 500)), visibility_radius_m=10
    )
    assert inside.land_fully_covers is True
    assert boundary_touch.land_fully_covers is False


def test_missing_chart_polygon_and_unknown_map_coverage_remain_unresolved():
    cell = RegionCell((100, 300, 400, 600))
    vm = VectorMap(
        box(-500, -500, 500, 500),
        crs="EPSG:32618",
        coverage_geometry=box(-50, 250, 350, 700),
    )
    result = build_region_evidence(vm, cell, visibility_radius_m=100, chart_catalog_complete=True)
    assert result.chart_coverage_complete is False
    assert result.map_evidence_covers_visibility_envelope is False
    assert result.land_fully_covers is False


def test_projection_preserves_duplicate_rows_and_unknown_types():
    records = (
        CatalogRecordV2("same", 32.78, -79.93, raw_type=None),
        CatalogRecordV2("same", 32.78, -79.93, raw_type="unclassified"),
    )
    objects = project_catalog_objects(records, target_crs="EPSG:32618")
    assert len(objects) == 2
    assert objects[0].key == objects[1].key == "same"
    assert all(not item.identity_proven for item in objects)
    assert all(item.position_uncertainty_m is None for item in objects)


def test_missing_projection_scale_certificate_is_not_invented():
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=box(0, 0, 1000, 1000))
    result = build_region_evidence(
        vm,
        RegionCell((100, 200, 100, 200)),
        visibility_radius_m=20,
        chart_coverage_geometry=box(50, 50, 250, 250),
        chart_catalog_complete=True,
        catalog_objects_cover_envelope=True,
    )
    assert result.certified_max_projection_scale is None
    assert result.map_evidence_covers_visibility_envelope is False
    assert result.chart_coverage_complete is False


def test_incomplete_object_coverage_blocks_chart_completeness():
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=box(0, 0, 1000, 1000))
    result = build_region_evidence(
        vm,
        RegionCell((100, 300, 400, 600)),
        visibility_radius_m=20,
        chart_coverage_geometry=box(50, 350, 350, 650),
        chart_catalog_complete=True,
        certified_max_projection_scale=1.0,
        catalog_objects_cover_envelope=False,
    )
    assert result.chart_coverage_complete is False


def test_explicit_unknown_region_stays_unknown():
    vm = VectorMap(
        box(0, 0, 800, 800),
        crs="EPSG:32618",
        coverage_geometry=box(0, 0, 1000, 1000),
        unknown_geometry=box(150, 450, 250, 550),
    )
    result = build_region_evidence(vm, RegionCell((100, 300, 400, 600)), visibility_radius_m=0)
    assert result.land_fully_covers is None


def test_asymmetric_cell_bounds_use_east_north_order():
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=box(0, 0, 1000, 1000))
    cell = RegionCell((100, 300, 400, 600))
    correct = box(100, 400, 300, 600)
    wrong_axis_order = box(100, 300, 400, 600)
    assert correct.area == 40_000
    assert wrong_axis_order.area == 90_000
    evidence = build_region_evidence(
        vm,
        cell,
        visibility_radius_m=10,
        certified_max_projection_scale=1.0,
    )
    assert evidence.map_evidence_covers_visibility_envelope


def test_mixed_land_geometry_proves_interior_without_collection_boundary():
    vm = VectorMap(box(0, 0, 800, 800), crs="EPSG:32618", coverage_geometry=box(-10, -10, 900, 900))
    vm._land = GeometryCollection([box(0, 0, 800, 800), LineString([(850, 0), (850, 800)])])
    inside = build_region_evidence(vm, RegionCell((100, 200, 100, 200)), visibility_radius_m=0)
    touching = build_region_evidence(vm, RegionCell((0, 100, 100, 200)), visibility_radius_m=0)
    assert inside.land_fully_covers is True
    assert touching.land_fully_covers is False
