"""Check screen/prediction parity when calibrated feature offsets move targets."""

from __future__ import annotations

import pytest

pytest.importorskip("maritime.search.visibility_screen")

from pyproj import CRS, Geod, Transformer  # noqa: E402
from shapely.geometry import box  # noqa: E402

from maritime.geometry.vector_map import VectorMap  # noqa: E402
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2  # noqa: E402
from maritime.search.prediction import (  # noqa: E402
    predict_visible_groups,
    prepare_prediction_context,
)
from maritime.search.visibility_screen import (  # noqa: E402
    screen_candidate_visibility,
)


def test_calibrated_feature_offset_uses_same_range_bearing_and_los_target():
    crs = CRS.from_epsg(32618)
    origin = (-76.3, 37.0)
    geod = Geod(ellps="WGS84")
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    east, north = map(float, transformer.transform(*origin))
    lon, lat, _ = geod.fwd(*origin, 0.0, 1000.0)
    record = CatalogRecordV2(
        key="offset-feature",
        longitude_deg=lon,
        latitude_deg=lat,
        normalized_type="physical_aid",
        metadata=(
            ("visible_feature_offset_east_m", 150.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )
    catalog = CatalogV2(
        records=(record,),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="independent-offset-review",
        catalog_version="1",
        catalog_id="offset-review",
    )
    vector_map = VectorMap(
        None,
        crs=crs,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )
    prepared = prepare_prediction_context(
        catalog,
        observer_crs=crs,
        visibility_radius_m=2000.0,
        vector_map=vector_map,
    )
    screen = screen_candidate_visibility((east, north), prepared, scan_all_members=True)
    prediction = predict_visible_groups(
        (east, north),
        catalog,
        observer_crs=crs,
        visibility_radius_m=2000.0,
        vector_map=vector_map,
        prepared_context=prepared,
    )
    assert screen.certified and screen.complete
    assert screen.exact_visible_group_count == len(prediction.groups) == 1
    group = prediction.groups[0]
    assert group.visible_feature_offset_quality == "calibrated"
    assert 8.0 < group.bearing_deg < 9.0
    assert 1000.0 < group.range_m < 1020.0


@pytest.mark.parametrize(
    "metadata",
    [
        (("source_feature_shift_m", 20.0),),
        (("target_offset_m", 20.0),),
        (("source_attributes", {"nested": {"length_m": 20.0}}),),
        (("source_components", [{"feature_id": "f", "beam_width_m": 4.0}]),),
    ],
)
def test_unknown_or_nested_model_metadata_invalidates_visibility_certificate(metadata):
    crs = CRS.from_epsg(32618)
    origin = (-76.3, 37.0)
    geod = Geod(ellps="WGS84")
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    east, north = map(float, transformer.transform(*origin))
    lon, lat, _ = geod.fwd(*origin, 0.0, 1000.0)
    record = CatalogRecordV2(
        key="unknown-model",
        longitude_deg=lon,
        latitude_deg=lat,
        normalized_type="physical_aid",
        metadata=metadata,
    )
    catalog = CatalogV2(
        records=(record,),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="independent-metadata-review",
        catalog_version="1",
        catalog_id="metadata-review",
    )
    vector_map = VectorMap(
        None,
        crs=crs,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )
    prepared = prepare_prediction_context(
        catalog,
        observer_crs=crs,
        visibility_radius_m=2000.0,
        vector_map=vector_map,
    )
    screen = screen_candidate_visibility((east, north), prepared)
    prediction = predict_visible_groups(
        (east, north),
        catalog,
        observer_crs=crs,
        visibility_radius_m=2000.0,
        vector_map=vector_map,
        prepared_context=prepared,
    )
    assert not screen.certified
    assert screen.reason == "unsupported_feature_model"
    assert screen.unresolved_model_member_keys == ("unknown-model",)
    # The predictor retains its current point-geometry semantics; the screen
    # must refuse certification so no pruning decision can use this metadata.
    assert len(prediction.groups) == 1


def test_resolved_enc_component_coordinate_provenance_is_geometry_inert():
    """A34 source component coordinates do not replace the top-level target."""
    crs = CRS.from_epsg(32618)
    origin = (-76.3, 37.0)
    geod = Geod(ellps="WGS84")
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    east, north = map(float, transformer.transform(*origin))
    lon, lat, _ = geod.fwd(*origin, 0.0, 1000.0)
    metadata = (
        ("identity_ambiguous", False),
        ("source_record_identity_status", "qualified_source_components"),
        ("physical_linkage_status", "resolved_by_chart_heuristic"),
        ("observation_association_status", "candidate_geometry_only"),
        ("identity_resolution", "same_chart_name_and_proximity"),
        ("physical_entity_id", "physical-aid-1"),
        ("source_attributes", {"objnam": "East Light", "height": 12.0}),
        ("source_component_count", 1),
        (
            "source_components",
            [
                {
                    "feature_id": "fid-1",
                    "chart_id": "US5VA10M",
                    "scale_band": "harbor",
                    "service_layer": "LIGHTS",
                    "longitude_deg": lon,
                    "latitude_deg": lat,
                    "source_epoch_sordat": None,
                }
            ],
        ),
        ("position_uncertainty_m", None),
        ("uncertainty_status", "unspecified_by_source"),
    )
    record = CatalogRecordV2(
        key="physical-aid-1",
        longitude_deg=lon,
        latitude_deg=lat,
        normalized_type="physical_aid",
        metadata=metadata,
    )
    catalog = CatalogV2(
        records=(record,),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="A34-shaped-metadata-review",
        catalog_version="1",
        catalog_id="resolved-component-review",
    )
    vector_map = VectorMap(
        None,
        crs=crs,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )
    prepared = prepare_prediction_context(
        catalog,
        observer_crs=crs,
        visibility_radius_m=2000.0,
        vector_map=vector_map,
    )
    screen = screen_candidate_visibility((east, north), prepared, scan_all_members=True)
    assert screen.certified and screen.exact_visible_group_count == 1
