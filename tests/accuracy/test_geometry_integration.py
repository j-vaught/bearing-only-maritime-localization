from __future__ import annotations

import json
from math import hypot

from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon, box, mapping, shape

from maritime.geometry.coordinates import (
    LocalFrame,
    geodesic_bearing_distance,
)
from maritime.geometry.vector_map import VectorMap
from maritime.importers.ais import catalog_from_ais_rows
from maritime.importers.enc import (
    ENCAid,
    ENCAidCatalog,
    ENCCollection,
    ENCFeature,
    combine_ais_and_enc_physical_catalogs,
    compose_fallback_map_v2,
    compose_partial_map_v2,
    compose_scale_fallback_map_v2,
    enc_aids_catalog_to_catalog_v2,
    group_physical_aids,
    mosaic_by_scale,
)
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
)
from maritime.search.prediction import predict_visible_groups
from maritime.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
)
from maritime.simulator.runner import run_simulator_v2


def _polygon_json(geometry) -> str:
    import json

    return json.dumps(mapping(geometry), sort_keys=True, separators=(",", ":"))


def test_combined_runtime_catalog_deduplicates_aid_components_and_keeps_raw_rows() -> None:
    aids = (
        ENCAid(
            "approach-beacon",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -76.0,
            36.0,
            (("objnam", "North Light"), ("sordat", "20240101")),
        ),
        ENCAid(
            "coastal-light",
            "chart-a",
            "coastal",
            "Coastal.Light_point",
            -76.00001,
            36.0,
            (("objnam", "North Light"), ("sordat", "20240201")),
        ),
        ENCAid(
            "ambiguous",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -75.9,
            36.0,
            (("objnam", "Unresolved Aid"),),
            identity_ambiguous=True,
        ),
        ENCAid(
            "unnamed",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -75.8,
            36.0,
            (),
        ),
        ENCAid(
            "chart-b-copy",
            "chart-b",
            "coastal",
            "Coastal.Light_point",
            -76.00002,
            36.0,
            (("objnam", "North Light"),),
        ),
    )
    aid_catalog = ENCAidCatalog(
        features=aids,
        physical_aids=group_physical_aids(aids),
        source="NOAA ENC Direct",
        retrieved_utc="2026-10-24T15:41:47Z",
    )
    ais = catalog_from_ais_rows(
        [
            {
                "source_key": "snapshot-row-1",
                "timestamp_utc": "2023-07-15T12:00:30Z",
                "longitude_deg": -76.2,
                "latitude_deg": 36.1,
                "vessel_type_code": "70",
            }
        ],
        target_time_utc="2023-07-15T12:00:30Z",
        source="AIS source snapshot",
    )

    raw = enc_aids_catalog_to_catalog_v2(aid_catalog)
    conflicted = combine_ais_and_enc_physical_catalogs(ais, aid_catalog)
    unconflicted_aids = ENCAidCatalog(
        features=aids[:4],
        physical_aids=group_physical_aids(aids[:4]),
        source=aid_catalog.source,
        retrieved_utc=aid_catalog.retrieved_utc,
    )
    combined = combine_ais_and_enc_physical_catalogs(ais, unconflicted_aids)

    assert len(raw.records) == len(aids)
    assert len(conflicted.records) == 1 + len(aids)
    assert len(combined.records) == 1 + 3
    assert [record.key for record in combined.records[:1]] == ["source:snapshot-row-1"]
    unresolved = [
        record
        for record in conflicted.records
        if dict(record.metadata).get("physical_linkage_status") == "unresolved"
    ]
    assert len(unresolved) == len(aids)
    assert {dict(record.metadata)["identity_resolution_reason"] for record in unresolved} >= {
        "cross_chart_same_name_proximity",
        "source_identity_ambiguous",
        "missing_name",
    }
    resolved = [
        record
        for record in combined.records
        if dict(record.metadata).get("identity_ambiguous") is False
    ]
    assert len(resolved) == 1
    physical = resolved[0]
    metadata = dict(physical.metadata)
    provenance = dict(physical.provenance)
    assert metadata["source_component_count"] == 1
    # The original combined record and its two ENC source features survive
    # losslessly inside the canonical input-record snapshot.
    original_metadata = dict(metadata["source_components"][0]["metadata"])
    assert original_metadata["source_component_count"] == 2
    assert {row["feature_id"] for row in original_metadata["source_components"]} == {
        "approach-beacon",
        "coastal-light",
    }
    assert provenance["source_epoch_sordat"] == ["20240101", "20240201"]
    assert physical.key.startswith("enc-physical:")
    assert len({record.key for record in conflicted.records}) == len(conflicted.records)
    assert ais.records[0].report_time_utc == combined.records[0].report_time_utc


def test_full_domain_map_composition_keeps_unknown_seams_and_partial_coverage() -> None:
    domain = box(-76.0, 36.0, -75.0, 37.0)
    coverage_a = box(-75.9, 36.1, -75.6, 36.4)
    unknown_a = box(-75.8, 36.1, -75.6, 36.4)
    land_a = box(-75.9, 36.1, -75.85, 36.4)
    coverage_b = box(-75.6, 36.1, -75.4, 36.4)
    first = MapV2(
        crs="EPSG:4326",
        source="fixture",
        map_version="fixture-a",
        coverage_status="partial",
        coverage_geometry_geojson=_polygon_json(coverage_a),
        unknown_geometry_geojson=_polygon_json(unknown_a),
        land_geometry_geojson=_polygon_json(land_a),
    )
    second = MapV2(
        crs="EPSG:4326",
        source="fixture",
        map_version="fixture-b",
        coverage_status="known",
        coverage_geometry_geojson=_polygon_json(coverage_b),
        land_geometry_geojson=None,
    )

    result = compose_partial_map_v2(
        (first, second),
        domain=domain,
        map_version="fixture-full-domain",
    )
    vector_map = VectorMap.from_map_v2(result, target_crs="EPSG:32618")
    project = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform

    assert result.coverage_status == "partial"
    assert vector_map.point_water_status(project(-75.88, 36.2)) == "land"
    assert vector_map.point_water_status(project(-75.7, 36.2)) == "unknown"
    assert vector_map.point_water_status(project(-75.5, 36.2)) == "water"
    assert vector_map.point_water_status(project(-75.2, 36.2)) == "unknown"
    assert vector_map.line_visibility(project(-75.5, 36.2), project(-75.2, 36.2)) == "unknown"


def test_fine_coast_map_overrides_coarse_land_and_falls_back_across_fine_unknowns() -> None:
    domain = box(-76.0, 36.0, -75.0, 37.0)
    fallback_coverage = box(-75.9, 36.1, -75.1, 36.9)
    fallback_land = box(-75.8, 36.2, -75.3, 36.8)
    fallback = MapV2(
        crs="EPSG:4326",
        source="complete General-scale fallback",
        map_version="general-fixture",
        coverage_status="known",
        coverage_geometry_geojson=_polygon_json(fallback_coverage),
        land_geometry_geojson=_polygon_json(fallback_land),
    )
    detailed_coverage = box(-75.75, 36.3, -75.25, 36.7)
    detailed_unknown = box(-75.55, 36.3, -75.25, 36.7)
    detailed_land = box(-75.72, 36.34, -75.68, 36.66)
    detailed = MapV2(
        crs="EPSG:4326",
        source="fine port map",
        map_version="fine-fixture",
        coverage_status="partial",
        coverage_geometry_geojson=_polygon_json(detailed_coverage),
        unknown_geometry_geojson=_polygon_json(detailed_unknown),
        land_geometry_geojson=_polygon_json(detailed_land),
    )

    result = compose_fallback_map_v2(
        fallback,
        (detailed,),
        domain=domain,
        map_version="fallback-composition-fixture",
        source="fine maps over complete General-scale fallback",
    )
    vector_map = VectorMap.from_map_v2(result, target_crs="EPSG:32618")
    project = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform

    assert result.coverage_status == "partial"
    assert vector_map.point_water_status(project(-75.7, 36.4)) == "land"
    # A narrow channel known to the fine map overrides coarse land.
    assert vector_map.point_water_status(project(-75.62, 36.4)) == "water"
    # The fine-map no-data seam falls back to General coverage and land.
    assert vector_map.point_water_status(project(-75.4, 36.4)) == "land"
    # General-scale coverage supplies water where the fine map has no authority.
    assert vector_map.point_water_status(project(-75.2, 36.4)) == "water"
    # Neither chart source covers this seam; it stays unknown.
    assert vector_map.point_water_status(project(-75.95, 36.4)) == "unknown"
    # Fine coverage beyond the requested map domain is cropped.
    assert vector_map.point_water_status(project(-75.05, 36.4)) == "unknown"


def test_true_north_prediction_and_simulation_share_range_los_and_merge_geometry() -> None:
    observer_lonlat = (-76.0, 36.0)
    frame = LocalFrame(observer_lonlat, "EPSG:32618")
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    observer_xy = forward.transform(*observer_lonlat)
    coverage = box(
        observer_xy[0] - 12_000,
        observer_xy[1] - 12_000,
        observer_xy[0] + 12_000,
        observer_xy[1] + 12_000,
    )
    land = Polygon(
        [
            (observer_xy[0] + 3_000, observer_xy[1] - 500),
            (observer_xy[0] + 3_500, observer_xy[1] - 500),
            (observer_xy[0] + 3_500, observer_xy[1] + 500),
            (observer_xy[0] + 3_000, observer_xy[1] + 500),
        ],
        holes=[
            [
                (observer_xy[0] + 3_100, observer_xy[1] - 100),
                (observer_xy[0] + 3_400, observer_xy[1] - 100),
                (observer_xy[0] + 3_400, observer_xy[1] + 100),
                (observer_xy[0] + 3_100, observer_xy[1] + 100),
            ]
        ],
    )
    vector_map = VectorMap(land, crs="EPSG:32618", coverage_geometry=coverage)
    target_a = frame.to_geographic(0.0, 2_000.0)
    target_b = frame.to_geographic(1.0, 2_010.0)
    records = tuple(
        CatalogRecordV2(
            key=key,
            longitude_deg=lon,
            latitude_deg=lat,
            raw_type="physical_aid",
            normalized_type="physical_aid",
            metadata=(("physical_entity_id", key),),
        )
        for key, (lon, lat) in (("a", target_a), ("b", target_b))
    )
    catalog = CatalogV2(
        records=records,
        timestamp_utc="2023-07-15T12:00:30Z",
        source="fixture",
        catalog_version="2",
        catalog_id="geometry-fixture",
    )

    prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs="EPSG:32618",
        visibility_radius_m=5_000.0,
        vector_map=vector_map,
        angular_resolution_deg=0.1,
    )
    simulation = run_simulator_v2(
        PhysicalSceneV2(catalog),
        KnownWaterObserver(*observer_lonlat),
        vector_map,
        SensorConfigurationV2(
            visibility_radius_m=5_000.0,
            angular_resolution_deg=0.1,
            bearing_sigma_deg=0.0,
            detection_probability=1.0,
            type_error_probability=0.0,
            expected_clutter_reports=0.0,
            outlier_probability=0.0,
        ),
        seed=12,
        timestamp_utc="2023-07-15T12:00:30Z",
    )
    expected = geodesic_bearing_distance(observer_lonlat, target_a)
    assert abs(prediction.groups[0].bearing_deg - expected.true_bearing_deg) < 1e-9
    assert prediction.groups[0].member_keys == ("a", "b")
    assert prediction.coverage_status == "known"
    assert simulation.diagnostics.merged_physical_group_count == len(prediction.groups)
    assert simulation.diagnostics.detected_physical_group_count == len(prediction.groups)
    assert len(simulation.measurements.measurements) == len(prediction.groups) == 1
    assert abs(hypot(*frame.to_local(target_a)) - expected.distance_m) < 0.7


def test_identity_limited_aid_remains_search_candidate_and_not_proven_support() -> None:
    crs = CRS.from_epsg(32618)
    to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    observer_lon, observer_lat = -76.3, 37.0
    target_lon, target_lat = -76.299, 37.0
    observer_east, observer_north = to_xy.transform(observer_lon, observer_lat)
    target_east, target_north = to_xy.transform(target_lon, target_lat)
    observer_xy = (float(observer_east), float(observer_north))
    target_xy = (float(target_east), float(target_north))
    coverage = box(
        min(observer_xy[0], target_xy[0]) - 2_000,
        min(observer_xy[1], target_xy[1]) - 2_000,
        max(observer_xy[0], target_xy[0]) + 2_000,
        max(observer_xy[1], target_xy[1]) + 2_000,
    )
    vector_map = VectorMap(None, crs=crs, coverage_geometry=coverage)
    record = CatalogRecordV2(
        key="enc-component:uncertain-aid",
        longitude_deg=target_lon,
        latitude_deg=target_lat,
        normalized_type="physical_aid",
        metadata=(("identity_ambiguous", True),),
    )
    background = CatalogRecordV2(
        key="enc-physical:resolved-background",
        longitude_deg=-76.298,
        latitude_deg=target_lat,
        normalized_type="physical_aid",
        metadata=(
            ("physical_entity_id", "resolved-background"),
            ("physical_linkage_status", "resolved_by_chart_heuristic"),
        ),
    )
    catalog = CatalogV2(
        records=(record, background),
        timestamp_utc="2023-07-15T12:00:30Z",
        source="test",
        catalog_version="1",
        catalog_id="unresolved-aid",
    )

    prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=crs,
        visibility_radius_m=1_000,
        vector_map=vector_map,
    )
    run = run_simulator_v2(
        PhysicalSceneV2(catalog),
        KnownWaterObserver(observer_lon, observer_lat),
        vector_map,
        SensorConfigurationV2(
            visibility_radius_m=1_000,
            angular_resolution_deg=0.1,
            detection_probability=1.0,
            expected_clutter_reports=0.0,
            outlier_probability=0.0,
        ),
        seed=5,
    )

    assert len(prediction.groups) == 1
    assert set(prediction.groups[0].member_keys) == {record.key, background.key}
    assert prediction.groups[0].representative_key == record.key
    assert prediction.identity_limited_keys == (record.key,)
    assert prediction.unresolved_model is False
    assert prediction.unresolved_model_keys == ()
    assert run.diagnostics.line_of_sight_visible_physical_count == 2
    assert len(run.diagnostics.groups) == 1
    assert set(run.diagnostics.groups[0].catalog_member_keys) == {record.key, background.key}
    assert run.diagnostics.groups[0].representative_catalog_key == record.key
    assert run.diagnostics.groups[0].identity_limited_catalog_member_keys == (record.key,)
    assert run.diagnostics.groups[0].catalog_member_physical_ids == ("resolved-background",)
    assert run.truth.objects[0].ais_record_id == f"catalog:{record.key}"
    blocked = VectorMap(
        box(
            min(observer_xy[0], target_xy[0]) - 2_000,
            min(observer_xy[1], target_xy[1]) - 2_000,
            max(observer_xy[0], target_xy[0]) + 2_000,
            max(observer_xy[1], target_xy[1]) + 2_000,
        ),
        crs=crs,
        coverage_geometry=coverage,
    )
    blocked_prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=crs,
        visibility_radius_m=1_000,
        vector_map=blocked,
    )
    assert blocked_prediction.unresolved_model is False
    assert blocked_prediction.unresolved_model_keys == ()


def test_unnamed_fid_and_snapshot_row_keep_source_identity_separate_from_linkage() -> None:
    aids = (
        ENCAid(
            "fid-row",
            "chart-a",
            "approach",
            "Approach.Beacon_SpecialPurpose_point",
            -76.0,
            36.0,
            (("approach.bcnsp.fid", "1088733"), ("objnam", "")),
        ),
        ENCAid(
            "snapshot-row",
            "chart-a",
            "approach",
            "Approach.Beacon_SpecialPurpose_point",
            -75.99,
            36.0,
            (("objnam", ""),),
            identity_ambiguous=True,
        ),
    )
    catalog = combine_ais_and_enc_physical_catalogs(
        CatalogV2(
            records=(),
            timestamp_utc="2023-07-15T12:00:30Z",
            source="AIS",
            catalog_version="1",
            catalog_id="empty-ais",
        ),
        ENCAidCatalog(aids, group_physical_aids(aids), "ENC", "2026-10-24T00:00:00Z"),
    )
    by_source = {dict(record.metadata)["source_feature_id"]: record for record in catalog.records}
    fid_metadata = dict(by_source["fid-row"].metadata)
    snapshot_metadata = dict(by_source["snapshot-row"].metadata)
    assert fid_metadata["source_record_identity_status"] == "qualified_source_key"
    assert snapshot_metadata["source_record_identity_status"] == "snapshot_row_derived"
    assert fid_metadata["physical_linkage_status"] == "unresolved"
    assert snapshot_metadata["physical_linkage_status"] == "unresolved"
    assert fid_metadata["observation_association_status"] == "candidate_geometry_only"
    assert snapshot_metadata["observation_association_status"] == "candidate_geometry_only"
    assert len(catalog.records) == 2


def test_cross_chart_duplicate_components_remain_candidates_but_are_identity_limited() -> None:
    aids = (
        ENCAid(
            "chart-a-row",
            "chart-a",
            "approach",
            "Approach.Light_point",
            -76.0,
            36.0,
            (("objnam", "East Channel Light"),),
        ),
        ENCAid(
            "chart-b-row",
            "chart-b",
            "coastal",
            "Coastal.Light_point",
            -76.00001,
            36.0,
            (("objnam", "East Channel Light"),),
        ),
    )
    catalog = combine_ais_and_enc_physical_catalogs(
        CatalogV2((), "2023-07-15T12:00:30Z", "AIS", "1", "empty-ais"),
        ENCAidCatalog(aids, group_physical_aids(aids), "ENC", "2026-10-24T00:00:00Z"),
    )
    observer_lon, observer_lat = -76.1, 36.0
    crs = CRS.from_epsg(32618)
    observer_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        observer_lon, observer_lat
    )
    coverage = box(
        observer_xy[0] - 20_000,
        observer_xy[1] - 20_000,
        observer_xy[0] + 20_000,
        observer_xy[1] + 20_000,
    )
    prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=crs,
        visibility_radius_m=20_000,
        vector_map=VectorMap(None, crs=crs, coverage_geometry=coverage),
        angular_resolution_deg=0.1,
    )
    assert len(catalog.records) == 2
    assert len(prediction.groups) == 1
    assert prediction.unresolved_model is False
    assert len(prediction.identity_limited_keys) == 2
    assert set(prediction.groups[0].member_keys) == {record.key for record in catalog.records}


def test_colocated_distinct_aids_make_one_detection() -> None:
    aids = (
        ENCAid(
            "north-row",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -76.0,
            36.0,
            (("objnam", "North Beacon"),),
        ),
        ENCAid(
            "south-row",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -76.0,
            36.0,
            (("objnam", "South Beacon"),),
        ),
    )
    catalog = combine_ais_and_enc_physical_catalogs(
        CatalogV2((), "2023-07-15T12:00:30Z", "AIS", "1", "empty-ais"),
        ENCAidCatalog(aids, group_physical_aids(aids), "ENC", "2026-10-24T00:00:00Z"),
    )
    observer_lon, observer_lat = -76.01, 36.0
    crs = CRS.from_epsg(32618)
    observer_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        observer_lon, observer_lat
    )
    coverage = box(
        observer_xy[0] - 5_000,
        observer_xy[1] - 5_000,
        observer_xy[0] + 5_000,
        observer_xy[1] + 5_000,
    )
    vector_map = VectorMap(None, crs=crs, coverage_geometry=coverage)
    prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=crs,
        visibility_radius_m=5_000,
        vector_map=vector_map,
        angular_resolution_deg=0.1,
    )
    simulation = run_simulator_v2(
        PhysicalSceneV2(catalog),
        KnownWaterObserver(observer_lon, observer_lat),
        vector_map,
        SensorConfigurationV2(
            visibility_radius_m=5_000,
            angular_resolution_deg=0.1,
            bearing_sigma_deg=0.0,
            detection_probability=1.0,
            expected_clutter_reports=0.0,
            outlier_probability=0.0,
        ),
        seed=6,
    )
    assert len(catalog.records) == 1
    assert dict(catalog.records[0].metadata)["source_component_count"] == 2
    assert len(prediction.groups) == 1
    assert len(prediction.groups[0].member_keys) == 1
    assert len(simulation.diagnostics.groups) == 1
    assert len(simulation.diagnostics.groups[0].catalog_member_physical_ids) == 1


def test_near_but_distinct_aids_remain_separate_groups() -> None:
    aids = (
        ENCAid(
            "west-row",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -76.0,
            36.0,
            (("objnam", "West Beacon"),),
        ),
        ENCAid(
            "east-row",
            "chart-a",
            "approach",
            "Approach.Beacon_Lateral_point",
            -75.999,
            36.0001,
            (("objnam", "East Beacon"),),
        ),
    )
    catalog = combine_ais_and_enc_physical_catalogs(
        CatalogV2((), "2023-07-15T12:00:30Z", "AIS", "1", "empty-ais"),
        ENCAidCatalog(aids, group_physical_aids(aids), "ENC", "2026-10-24T00:00:00Z"),
    )
    observer_lon, observer_lat = -76.01, 36.0
    crs = CRS.from_epsg(32618)
    observer_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        observer_lon, observer_lat
    )
    coverage = box(
        observer_xy[0] - 5_000,
        observer_xy[1] - 5_000,
        observer_xy[0] + 5_000,
        observer_xy[1] + 5_000,
    )
    prediction = predict_visible_groups(
        observer_xy,
        catalog,
        observer_crs=crs,
        visibility_radius_m=5_000,
        vector_map=VectorMap(None, crs=crs, coverage_geometry=coverage),
        angular_resolution_deg=0.05,
    )
    assert len(catalog.records) == 2
    assert len(prediction.groups) == 2
    assert len({record.key for record in catalog.records}) == 2


def _map_v2(coverage, land, *, unknown=None, version="fixture"):
    status = "partial" if unknown is not None else "known"
    return MapV2(
        crs="EPSG:4326",
        source="fixture",
        map_version=version,
        coverage_status=status,
        coverage_geometry_geojson=_polygon_json(coverage),
        unknown_geometry_geojson=_polygon_json(unknown) if unknown is not None else None,
        land_geometry_geojson=_polygon_json(land) if not land.is_empty else None,
    )


def test_scale_fallback_prioritizes_detail_and_preserves_coarse_unknown_fallback() -> None:
    domain = box(0, 0, 10, 10)
    coarse = _map_v2(domain, domain, version="general-base")
    approach_coverage = box(2, 2, 8, 8)
    approach_land = box(2, 2, 8, 5).difference(box(6, 3, 7, 4))
    approach_unknown = box(7, 2, 8, 4)
    approach = _map_v2(
        approach_coverage,
        approach_land,
        unknown=approach_unknown,
        version="approach",
    )
    harbour_coverage = box(3, 3, 5, 5)
    harbour = _map_v2(harbour_coverage, Polygon(), version="harbour")
    result, conflicts = compose_scale_fallback_map_v2(
        coarse,
        {"approach": [approach], "harbour": [harbour]},
        domain=domain,
        map_version="composed",
        source="fixture",
        densify_geographic_edges=False,
    )
    assert result.coverage_geometry_geojson is not None
    assert result.land_geometry_geojson is not None
    coverage = shape(json.loads(result.coverage_geometry_geojson))
    land = shape(json.loads(result.land_geometry_geojson))
    assert conflicts["approach"] == 0.0
    assert result.coverage_status == "known"
    assert result.unknown_geometry_geojson is None
    assert coverage.covers(Point(4, 4))
    assert not land.covers(Point(4, 4))  # Harbour water overrides approach and coarse land.
    assert not land.covers(Point(6.5, 3.5))  # Narrow approach water channel is retained.
    assert land.covers(Point(7.25, 3.0))  # Fine unknown seam falls back to coarse land.
    assert not coverage.covers(Point(11, 11))
    assert not coverage.covers(Point(11, 11))  # Out-of-domain is not inferred as water.


def test_segmentized_scale_output_preserves_shoreline_and_los_in_search_crs() -> None:
    domain = box(-75.2, 39.8, -74.8, 40.2)
    land = Polygon([(-75.1, 39.9), (-74.9, 40.1), (-74.9, 40.2), (-75.1, 40.2)])
    fallback = _map_v2(domain, land)
    compact, _ = compose_scale_fallback_map_v2(
        fallback,
        {},
        domain=domain,
        map_version="compact",
        source="fixture",
        densify_geographic_edges=False,
    )
    segmentized, _ = compose_scale_fallback_map_v2(
        fallback,
        {},
        domain=domain,
        map_version="segmentized",
        source="fixture",
        densify_geographic_edges=False,
        geographic_segment_length_deg=0.001,
    )
    reference, _ = compose_scale_fallback_map_v2(
        fallback,
        {},
        domain=domain,
        map_version="fine-reference",
        source="fixture",
        densify_geographic_edges=False,
        geographic_segment_length_deg=0.0001,
    )
    compact_vector = VectorMap.from_map_v2(compact, target_crs="EPSG:32618")
    segmentized_vector = VectorMap.from_map_v2(segmentized, target_crs="EPSG:32618")
    reference_vector = VectorMap.from_map_v2(reference, target_crs="EPSG:32618")
    project = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    start = project.transform(-75.1, 39.9)
    end = project.transform(-74.9, 40.1)
    tangent_x = end[0] - start[0]
    tangent_y = end[1] - start[1]
    tangent_length = hypot(tangent_x, tangent_y)
    normal = (-tangent_y / tangent_length, tangent_x / tangent_length)
    shore = project.transform(-75.0, 40.0)
    samples = [(shore[0] + side * normal[0], shore[1] + side * normal[1]) for side in (-1.0, 1.0)]
    reference_status = [reference_vector.point_water_status(point) for point in samples]
    assert [segmentized_vector.point_water_status(point) for point in samples] == reference_status
    assert [compact_vector.point_water_status(point) for point in samples] != reference_status
    observer = (
        shore[0] + 100.0 * normal[0],
        shore[1] + 100.0 * normal[1],
    )
    target = (
        shore[0] - 100.0 * normal[0],
        shore[1] - 100.0 * normal[1],
    )
    assert segmentized_vector.line_visibility(observer, target) == reference_vector.line_visibility(
        observer, target
    )


def test_scale_fallback_marks_same_scale_land_water_disagreement_unknown() -> None:
    domain = box(0, 0, 10, 10)
    coarse = _map_v2(domain, domain)
    overlap = box(2, 2, 5, 5)
    land_source = _map_v2(overlap, overlap, version="approach-land")
    water_source = _map_v2(overlap, Polygon(), version="approach-water")
    result, conflicts = compose_scale_fallback_map_v2(
        coarse,
        {"approach": [land_source, water_source]},
        domain=domain,
        map_version="conflict",
        source="fixture",
        densify_geographic_edges=False,
    )
    assert result.coverage_geometry_geojson is not None
    assert result.unknown_geometry_geojson is not None
    coverage = shape(json.loads(result.coverage_geometry_geojson))
    unknown = shape(json.loads(result.unknown_geometry_geojson))
    assert conflicts["approach"] == overlap.area
    assert not coverage.covers(Point(3, 3))
    assert unknown.covers(Point(3, 3))
    assert coverage.covers(Point(1, 1))


def test_scale_mosaic_drops_zero_area_boundary_contacts_before_precedence_overlay() -> None:
    domain = box(0, 0, 10, 10)
    coastal = ENCFeature(
        feature_id="coastal-boundary-contact",
        chart_id="chart-coastal",
        scale_band="coastal",
        layer="Coastal.Coverage_area",
        geometry=box(10, 0, 12, 10),
        attributes=(("CATCOV", "coverage available"),),
    )
    general_coverage = ENCFeature(
        feature_id="general-coverage",
        chart_id="chart-general",
        scale_band="general",
        layer="General.Coverage_area",
        geometry=domain,
        attributes=(("CATCOV", "coverage available"),),
    )
    layers = mosaic_by_scale(
        ENCCollection("EPSG:4326", (coastal, general_coverage), "boundary-contact"),
        domain,
    )
    assert [layer.scale_band for layer in layers] == ["general"]
    assert layers[0].coverage.equals(domain)
