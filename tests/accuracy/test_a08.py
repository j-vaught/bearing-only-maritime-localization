from __future__ import annotations

import csv
import json

import pytest
from shapely.geometry import Point, Polygon, shape

from maritime_localization_ingest.enc import (
    ENCAid,
    ENCCollection,
    ENCFeature,
    enc_aids_catalog_to_catalog_v2,
    group_physical_aids,
    load_enc_aids_csv,
    load_enc_geojson,
    load_enc_physical_aid_catalog,
    mosaic_by_scale,
    mosaic_map_v2,
)


def feature(
    identity: str,
    band: str,
    layer: str,
    geometry: Polygon,
    **attrs: object,
) -> ENCFeature:
    return ENCFeature(
        feature_id=identity,
        chart_id="US5NY123.000",
        scale_band=band,
        layer=f"{band.title()}.{layer}",
        geometry=geometry,
        attributes=tuple(sorted(attrs.items())),
        epoch="20261001",
    )


def test_scale_precedence_clips_geometry_without_coordinate_deduplication() -> None:
    domain = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    coarse_coverage = feature("coarse-cov", "coastal", "Coverage_area", domain, CATCOV=1)
    coarse_land = feature(
        "coarse-land",
        "coastal",
        "Land_Area",
        Polygon([(0, 0), (10, 0), (10, 2), (0, 2)]),
    )
    detailed_coverage = feature(
        "detail-cov",
        "berthing",
        "Coverage_area",
        Polygon([(2, 2), (8, 2), (8, 8), (2, 8)]),
        CATCOV=1,
    )
    detailed_land = feature(
        "detail-land",
        "berthing",
        "Land_Area",
        Polygon([(2, 2), (8, 2), (8, 4), (2, 4)]),
    )
    # A different source feature at the same coordinate remains a distinct row.
    second_land = feature(
        "detail-land-topmark",
        "berthing",
        "Land_Area",
        Polygon([(2, 2), (8, 2), (8, 4), (2, 4)]),
    )
    collection = ENCCollection(
        "EPSG:4326",
        (coarse_coverage, coarse_land, detailed_coverage, detailed_land, second_land),
        "fixture",
    )

    layers = mosaic_by_scale(collection, domain)

    assert [layer.scale_band for layer in layers] == ["berthing", "coastal"]
    detailed, coarse = layers
    assert detailed.coverage.area == pytest.approx(36)
    assert coarse.coverage.area == pytest.approx(64)
    assert detailed.land.area == pytest.approx(12)
    assert coarse.land.area == pytest.approx(20)
    assert detailed.coverage.intersection(coarse.coverage).area == pytest.approx(0)
    assert set(detailed.feature_ids) == {"detail-cov", "detail-land", "detail-land-topmark"}


def test_catcov_no_coverage_is_subtracted_and_remains_unknown() -> None:
    domain = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    coverage = feature("coverage", "approach", "Coverage_area", domain, CATCOV=1)
    excluded = feature(
        "no-data",
        "approach",
        "Coverage_area",
        Polygon([(4, 4), (6, 4), (6, 6), (4, 6)]),
        CATCOV=2,
    )
    collection = ENCCollection("EPSG:4326", (coverage, excluded), "catcov")
    result = mosaic_map_v2(collection, domain, map_version="fixture-1")
    assert result.coverage_geometry_geojson is not None
    assert result.unknown_geometry_geojson is not None
    known = shape(json.loads(result.coverage_geometry_geojson))
    unknown = shape(json.loads(result.unknown_geometry_geojson))

    assert result.coverage_status == "partial"
    assert known.area == pytest.approx(96)
    assert unknown.area == pytest.approx(4)
    assert unknown.covers(Point(5, 5))


def test_missing_coverage_never_infers_water() -> None:
    domain = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    collection = ENCCollection("EPSG:4326", (), "empty")
    result = mosaic_map_v2(collection, domain, map_version="unknown")
    assert result.coverage_status == "unknown"
    assert result.coverage_geometry_geojson is None
    assert result.unknown_geometry_geojson is None


def test_incomplete_scale_is_unknown_and_complete_coarser_fallback_survives() -> None:
    domain = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    detailed = (
        feature("fine-cov", "approach", "Coverage_area", domain, CATCOV=1),
        feature("fine-land", "approach", "Land_Area", domain),
    )
    coarse = (
        feature("coarse-cov", "general", "Coverage_area", domain, CATCOV=1),
        feature("coarse-land", "general", "Land_Area", domain),
    )
    collection = ENCCollection("EPSG:4326", detailed + coarse, "fallback")
    layers = mosaic_by_scale(collection, domain, incomplete_scale_bands={"approach"})
    assert [layer.scale_band for layer in layers] == ["general"]
    assert layers[0].coverage.area == pytest.approx(100)


def test_invalid_polygon_features_are_rejected() -> None:
    with pytest.raises(ValueError, match="invalid geometry"):
        feature(
            "invalid",
            "berthing",
            "Land_Area",
            Polygon([(0, 0), (2, 2), (0, 2), (2, 0)]),
        )


def test_geojson_loader_retains_source_chart_epoch_and_distinct_colocated_ids(tmp_path) -> None:
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    source = {
        "type": "FeatureCollection",
        "name": "tiny",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "scale_band": "berthing",
                    "service_layer": "Berthing.Land_Area",
                    "DSNM": "US5NY123.000",
                    "LNAM": "aid-a",
                    "SORDAT": "20261001",
                    "CATCOV": "1",
                },
                "geometry": geom,
            },
            {
                "type": "Feature",
                "properties": {
                    "scale_band": "berthing",
                    "service_layer": "Berthing.Land_Area",
                    "DSNM": "US5NY123.000",
                    "LNAM": "aid-b",
                    "SORDAT": "20261001",
                    "CATCOV": "1",
                },
                "geometry": geom,
            },
            {
                "type": "Feature",
                "properties": {
                    "scale_band": "berthing",
                    "service_layer": "Berthing.Land_Area",
                    "DSNM": "US5NY123.000",
                    "LNAM": "aid-b",
                    "SORDAT": "20261001",
                    "CATCOV": "1",
                },
                "geometry": geom,
            },
        ],
    }
    path = tmp_path / "features.geojson"
    path.write_text(json.dumps(source), encoding="utf-8")

    loaded = load_enc_geojson(path)

    assert len(loaded.features) == 3
    assert len({row.feature_id for row in loaded.features}) == 3
    assert {row.feature_id for row in loaded.features} == {"aid-a", "aid-b", "aid-b:repeat-1"}
    assert [row.identity_ambiguous for row in loaded.features] == [False, False, True]
    assert {row.chart_id for row in loaded.features} == {"US5NY123.000"}
    assert {row.epoch for row in loaded.features} == {"20261001"}


def test_aid_csv_retains_colocated_cross_scale_rows_and_marks_identity_ambiguity(tmp_path) -> None:
    path = tmp_path / "aids.csv"
    fields = [
        "scale_band",
        "service_layer",
        "source_object_id",
        "longitude",
        "latitude",
        "dsnm",
        "objnam",
        "approach.light.fid",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "scale_band": "approach",
                "service_layer": "Approach.Light_point",
                "longitude": "-76.0",
                "latitude": "36.0",
                "dsnm": "US5VA11.000",
                "objnam": "Beacon",
                "approach.light.fid": "123",
            }
        )
        writer.writerow(
            {
                "scale_band": "approach",
                "service_layer": "Approach.Topmark_point",
                "longitude": "-76.0",
                "latitude": "36.0",
                "dsnm": "US5VA11.000",
                "objnam": "Beacon",
                "approach.light.fid": "456",
            }
        )
        writer.writerow(
            {
                "scale_band": "coastal",
                "service_layer": "Coastal.Light_point",
                "longitude": "-76.0",
                "latitude": "36.0",
                "dsnm": "US5VA11.000",
                "objnam": "Beacon",
            }
        )
        writer.writerow(
            {
                "scale_band": "coastal",
                "service_layer": "Coastal.Light_point",
                "longitude": "-76.0",
                "latitude": "36.0",
                "dsnm": "US5VA11.000",
                "objnam": "Beacon",
            }
        )

    aids = load_enc_aids_csv(path)

    assert len(aids) == 4
    assert len({aid.feature_id for aid in aids}) == 4
    assert aids[0].feature_id != aids[1].feature_id
    assert aids[2].identity_ambiguous and aids[3].identity_ambiguous
    assert aids[0].chart_id == "US5VA11.000"


def test_physical_catalog_groups_components_and_flags_colocated_ambiguity(tmp_path) -> None:
    path = tmp_path / "aids.csv"
    fields = [
        "scale_band",
        "service_layer",
        "source_object_id",
        "longitude",
        "latitude",
        "dsnm",
        "objnam",
        "sordat",
        "approach.bcnlat.fid",
        "approach.lights.fid",
        "approach.topmar.fid",
    ]
    rows = [
        (
            "approach",
            "Approach.Beacon_Lateral_point",
            "-76.0",
            "36.0",
            "North Range Light",
            "20240101",
            "100",
            "",
            "",
        ),
        (
            "approach",
            "Approach.Light_point",
            "-76.00001",
            "36.0",
            "North Range Light",
            "20240102",
            "",
            "101",
            "",
        ),
        (
            "approach",
            "Approach.Topmark_point",
            "-76.00002",
            "36.0",
            "North Range Light",
            "20240103",
            "",
            "",
            "102",
        ),
        (
            "coastal",
            "Coastal.Light_point",
            "-76.0",
            "36.0",
            "North Range Light",
            "20240104",
            "",
            "201",
            "",
        ),
        (
            "coastal",
            "Coastal.Light_point",
            "-76.0",
            "36.0",
            "North Range Light",
            "20240105",
            "",
            "202",
            "",
        ),
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for band, layer, longitude, latitude, name, epoch, beacon, light, topmark in rows:
            writer.writerow(
                [
                    band,
                    layer,
                    "",
                    longitude,
                    latitude,
                    "US5VA11.000",
                    name,
                    epoch,
                    beacon,
                    light,
                    topmark,
                ]
            )
    (tmp_path / "aids.manifest.json").write_text(
        json.dumps({"source": "NOAA ENC Direct", "retrieved_utc": "2026-10-25T00:00:00Z"}),
        encoding="utf-8",
    )

    catalog = load_enc_physical_aid_catalog(path)

    assert catalog.source == "NOAA ENC Direct"
    assert catalog.retrieved_utc == "2026-10-25T00:00:00Z"
    assert len(catalog.features) == 5
    assert all(not aid.identity_ambiguous for aid in catalog.features)
    assert all(any(key.endswith(".fid") for key, _ in aid.attributes) for aid in catalog.features)
    assert len(catalog.physical_aids) == 2
    combined = next(aid for aid in catalog.physical_aids if len(aid.feature_ids) == 4)
    duplicate = next(aid for aid in catalog.physical_aids if len(aid.feature_ids) == 1)
    assert set(combined.component_types) == {"beacon", "light", "topmark"}
    assert combined.identity_ambiguous
    assert duplicate.identity_ambiguous


def test_physical_grouping_does_not_join_across_charts_or_far_positions() -> None:
    attributes = (("objnam", "Range Light"),)
    rows = (
        ENCAid("a", "chart-a", "approach", "Approach.Light_point", -76.0, 36.0, attributes),
        ENCAid("b", "chart-b", "coastal", "Coastal.Topmark_point", -76.0, 36.0, attributes),
        ENCAid("c", "chart-a", "coastal", "Coastal.Topmark_point", -75.9, 36.0, attributes),
    )

    groups = group_physical_aids(rows)

    assert len(groups) == 3
    assert all(len(group.feature_ids) == 1 for group in groups)
    assert all(not group.identity_ambiguous for group in groups)


def test_catalog_v2_emits_physical_identity_only_when_resolved(tmp_path) -> None:
    path = tmp_path / "catalog.csv"
    fields = [
        "scale_band",
        "service_layer",
        "source_object_id",
        "longitude",
        "latitude",
        "dsnm",
        "objnam",
        "sordat",
        "approach.light.fid",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "scale_band": "approach",
                "service_layer": "Approach.Light_point",
                "longitude": "-76.0",
                "latitude": "36.0",
                "dsnm": "US5VA11.000",
                "objnam": "South Range Light",
                "sordat": "20240101",
                "approach.light.fid": "42",
            }
        )
        writer.writerow(
            {
                "scale_band": "approach",
                "service_layer": "Approach.Beacon_Lateral_point",
                "longitude": "-76.1",
                "latitude": "36.1",
                "dsnm": "US5VA11.000",
                "sordat": "20240102",
            }
        )
    (tmp_path / "catalog.manifest.json").write_text(
        json.dumps({"source": "NOAA ENC Direct", "retrieved_utc": "2026-10-25T00:00:00Z"}),
        encoding="utf-8",
    )

    catalog = load_enc_physical_aid_catalog(path)
    output = enc_aids_catalog_to_catalog_v2(catalog)
    by_name = {
        dict(record.metadata)["source_attributes"].get("objnam"): dict(record.metadata)
        for record in output.records
    }

    assert by_name["South Range Light"]["physical_entity_id"]
    assert "physical_entity_id" not in by_name[""]
