"""Normalized chart-aid identity and lossless source-component contract checks."""

import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from math import copysign, nextafter

import pytest

import maritime.catalog_identity as identity_module
from maritime.catalog_identity import (
    COMPONENT_SCHEMA,
    COORDINATE_DECIMAL_PLACES,
    COORDINATE_MERGE_RADIUS_M,
    RULE,
    canonical_aid_key,
    canonicalize_chart_aids,
    canonicalize_chart_records,
    chart_coordinate_groups,
    is_chart_aid,
    normalize_aid_coordinate,
)
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.prediction import _visible_feature_offset


def record(key, lat=32.7, lon=-79.9, kind="light", *, metadata=(), provenance=(), **kwargs):
    return CatalogRecordV2(
        key,
        lat,
        lon,
        raw_type=kind,
        metadata=tuple(metadata),
        provenance=tuple(provenance),
        **kwargs,
    )


def catalog(*rows):
    return CatalogV2(tuple(rows), "2023-07-15T12:00:30Z", "fixture", "1", "exact-coordinates")


def json_value(value):
    return json.loads(json.dumps(value, sort_keys=True))


def test_normalized_coordinates_merge_components_but_distinct_locations_do_not():
    first = record("beacon", kind="beacon")
    second = record("light", kind="light")
    near = record("near", lat=32.7 + 1e-5)
    assert chart_coordinate_groups([first, second, near]) == ((0, 1), (2,))
    result = canonicalize_chart_aids(catalog(first, second, near))
    assert len(result.records) == 2
    merged = next(row for row in result.records if row.key != "near")
    assert merged.key == canonical_aid_key(32.7, -79.9)
    assert merged.normalized_type == "physical_aid"
    assert (
        next(row for row in result.records if row.key == "near").latitude_deg
        == normalize_aid_coordinate(near.latitude_deg, near.longitude_deg)[0]
    )
    assert canonical_aid_key(0.1 + 0.2, 0.0) == canonical_aid_key(0.3, 0.0)


def test_signed_zero_is_one_exact_coordinate_and_single_keys_are_preserved():
    assert canonical_aid_key(-0.0, 0.0) == canonical_aid_key(0.0, -0.0)
    rows = canonicalize_chart_records([record("a", -0.0, 0.0), record("b", 0.0, -0.0)])
    assert len(rows) == 1
    assert copysign(1.0, rows[0].latitude_deg) == 1.0
    singleton = canonicalize_chart_records([record("daymark", kind="daymark")])[0]
    assert singleton.key == "daymark"
    assert singleton.normalized_type == "physical_aid"


@pytest.mark.parametrize("kind", ["physical_aid", "light", "beacon", "buoy", "daymark", "topmark"])
def test_explicit_chart_component_types_are_eligible(kind):
    assert is_chart_aid(record("aid", kind=kind))


def test_noaa_component_layers_are_eligible_without_generic_unknown_promotion():
    hinted = [
        record("a", kind="unknown", provenance=(("source_layer", "Harbor.Light_point"),)),
        record("b", kind="unknown", metadata=(("service_layer", "Coastal.Topmark_point"),)),
        record("c", kind="unknown", provenance=(("source_layers", ["Overview.Daymark_point"]),)),
        record(
            "d",
            kind="unknown",
            metadata=(
                (
                    "source_components",
                    [
                        {"service_layer": "Approach.Beacon_Lateral_point"},
                    ],
                ),
            ),
        ),
        record("enc-component:Overview.Topmark_point:source=7", kind="unknown"),
    ]
    assert all(is_chart_aid(row) for row in hinted)
    unrelated = [
        record("unknown", kind="unknown"),
        record("enc-component:generic:source=7", kind="unknown"),
        record("sounding", kind="unknown", provenance=(("source_layer", "Coastal.Soundg_point"),)),
        record("noaa", kind="unknown", provenance=(("source", "NOAA ENC"),)),
    ]
    assert not any(is_chart_aid(row) for row in unrelated)


def test_vessels_virtuals_and_unrelated_unknown_rows_are_untouched():
    vessel = record("vessel", kind="vessel", provenance=(("source_layer", "Harbor.Light_point"),))
    virtual = record(
        "virtual", kind="virtual_aid", metadata=(("source_layer", "Harbor.Light_point"),)
    )
    virtual_flag = record("virtual-flag", kind="buoy", metadata=(("is_virtual", True),))
    unknown = record("unrelated", kind="unknown")
    aid = record("aid", kind="daymark")
    output = canonicalize_chart_records([vessel, virtual, virtual_flag, unknown, aid])
    by_key = {row.key: row for row in output}
    for original in (vessel, virtual, virtual_flag, unknown):
        assert by_key[original.key] is original
        assert not is_chart_aid(original)
    assert len(output) == 5


def test_complete_sources_provenance_geometry_and_idempotence_are_preserved():
    one = record(
        "a",
        kind="beacon",
        metadata=(
            ("position_uncertainty_m", 3.0),
            ("angular_half_width_deg", 0.1),
            ("visible_feature_offset_east_m", 2.0),
            ("visible_feature_offset_north_m", 4.0),
            ("custom_payload", {"original": [1, 2, {"x": True}]}),
        ),
        provenance=(("source_chart_id", "chart-a"), ("custom_origin", "one")),
        quality=0.7,
        report_time_utc="2023-07-15T12:00:30Z",
    )
    two = record(
        "b",
        kind="light",
        metadata=(
            ("position_uncertainty_m", 7.0),
            ("angular_half_width_deg", 0.4),
            ("visible_feature_offset_east_m", 2.0),
            ("visible_feature_offset_north_m", 4.0),
        ),
        provenance=(("source_chart_id", "chart-b"),),
    )
    source = catalog(two, one)
    result = canonicalize_chart_aids(source)
    assert result == canonicalize_chart_aids(catalog(one, two))
    assert result == canonicalize_chart_aids(result)
    merged = result.records[0]
    metadata = dict(merged.metadata)
    assert metadata["identity_resolution"] == RULE
    assert metadata["source_components_schema"] == COMPONENT_SCHEMA
    assert metadata["source_components"] == [json_value(asdict(one)), json_value(asdict(two))]
    assert metadata["position_uncertainty_m"] == 7.0
    assert metadata["angular_half_width_deg"] == 0.4
    assert _visible_feature_offset(merged) == (2.0, 4.0, "calibrated")
    assert dict(merged.provenance)["custom_origin"] == "one"
    assert dict(merged.provenance)["source_component_keys"] == ["a", "b"]


def test_unknown_member_position_bound_stays_unknown_without_alias_fallback():
    one = record("a", metadata=(("horizontal_uncertainty_m", 20.0),))
    two = record("b")
    merged = canonicalize_chart_records([one, two])[0]
    metadata = dict(merged.metadata)
    assert metadata["position_uncertainty_m"] is None
    assert "horizontal_uncertainty_m" not in metadata
    assert "uncertainty_m" not in metadata


def test_conflicting_offsets_keep_one_object_but_unbounded_geometry():
    one = record(
        "a",
        metadata=(
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", 0.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )
    two = record(
        "b",
        metadata=(
            ("position_uncertainty_m", 0.0),
            ("visible_feature_offset_east_m", 1.0),
            ("visible_feature_offset_north_m", 0.0),
        ),
    )
    output = canonicalize_chart_records([one, two])
    assert len(output) == 1
    metadata = dict(output[0].metadata)
    assert metadata["position_uncertainty_m"] is None
    assert metadata["visible_feature_offset_geometry_unsupported"] is True
    assert _visible_feature_offset(output[0])[2] == "unsupported"
    assert len(metadata["source_components"]) == 2


def test_preexisting_importer_components_stay_lossless_and_do_not_nest_again():
    original = record(
        "old",
        metadata=(
            (
                "source_components",
                [
                    {
                        "feature_id": "f1",
                        "service_layer": "Harbor.Light_point",
                        "latitude_deg": 32.7,
                    },
                ],
            ),
        ),
    )
    first = canonicalize_chart_records([original])
    second = canonicalize_chart_records(first)
    assert first == second
    components = dict(second[0].metadata)["source_components"]
    assert components == [json_value(asdict(original))]
    assert dict(components[0]["metadata"])["source_components"][0]["feature_id"] == "f1"


def test_shared_prior_identity_across_different_coordinates_remains_ambiguous():
    rows = [
        record("a", lat=32.0, metadata=(("physical_entity_id", "old-a"),)),
        record("b-a", lat=33.0, metadata=(("physical_entity_id", "old-a"),)),
        record("b-b", lat=33.0, metadata=(("physical_entity_id", "old-b"),)),
        record("c", lat=34.0, metadata=(("physical_entity_id", "old-b"),)),
    ]
    output = canonicalize_chart_records(rows)
    assert len(output) == 3
    assert len({dict(row.metadata)["physical_entity_id"] for row in output}) == 1
    assert all(dict(row.metadata)["physical_linkage_status"] == "unresolved" for row in output)
    assert all(dict(row.metadata)["identity_ambiguous"] for row in output)
    assert output == canonicalize_chart_records(output)


def test_generated_key_collision_never_overwrites_an_untouched_vessel():
    collision = canonical_aid_key(32.7, -79.9)
    with pytest.raises(ValueError, match="collides"):
        canonicalize_chart_records([record("a"), record("b"), record(collision, kind="vessel")])


def test_shared_prior_identity_with_untouched_non_aid_stays_ambiguous():
    aid = record("aid", metadata=(("physical_entity_id", "shared"),))
    vessel = record("vessel", lat=33.0, kind="vessel", metadata=(("physical_entity_id", "shared"),))
    output = canonicalize_chart_records([aid, vessel])
    canonical = next(row for row in output if row.key == "aid")
    assert dict(canonical.metadata)["identity_ambiguous"] is True
    assert dict(canonical.metadata)["physical_linkage_status"] == "unresolved"
    assert dict(canonical.metadata)["physical_entity_id"] == "shared"
    assert next(row for row in output if row.key == "vessel") is vessel


def test_unmerged_sources_do_not_gain_identity_certificates():
    bare = record("bare", kind="daymark")
    unresolved = record(
        "unresolved",
        lat=33.0,
        kind="topmark",
        metadata=(
            ("physical_entity_id", "known-record-not-proven-object"),
            ("identity_ambiguous", True),
            ("physical_linkage_status", "unresolved"),
        ),
    )
    complete = record(
        "resolved",
        lat=34.0,
        metadata=(
            ("physical_entity_id", "trusted"),
            ("identity_ambiguous", False),
            ("physical_linkage_status", "resolved"),
        ),
    )
    output = canonicalize_chart_records([bare, unresolved, complete])
    by_key = {row.key: row for row in output}
    for source in (bare, unresolved, complete):
        original_meta = dict(source.metadata)
        result_meta = dict(by_key[source.key].metadata)
        for name in ("physical_entity_id", "identity_ambiguous", "physical_linkage_status"):
            assert (name in result_meta) == (name in original_meta)
            assert result_meta.get(name) == original_meta.get(name)
        assert by_key[source.key].normalized_type == "physical_aid"
    assert output == canonicalize_chart_records(output)


def test_nonaid_partition_precedes_aids_with_deterministic_key_order():
    aids = [record("a-aid", lat=32.0), record("z-aid", lat=33.0)]
    vessel = record("z-vessel", kind="vessel")
    unknown = record("b-unknown", kind="unknown")
    rows = canonicalize_chart_records([aids[1], vessel, aids[0], unknown])
    assert [row.key for row in rows] == ["b-unknown", "z-vessel", "a-aid", "z-aid"]
    assert rows == canonicalize_chart_records([unknown, aids[0], vessel, aids[1]])
    assert rows[0] is unknown and rows[1] is vessel


@pytest.fixture
def empty_catalog_memo(monkeypatch):
    monkeypatch.setattr(identity_module, "_catalog_cache", OrderedDict())
    calls = []
    original = identity_module.canonicalize_chart_records

    def count_calls(records):
        calls.append(records)
        return original(records)

    monkeypatch.setattr(identity_module, "canonicalize_chart_records", count_calls)
    return calls


def test_catalog_identity_memo_reuses_original_and_canonical_without_deep_work(empty_catalog_memo):
    source = catalog(record("b"), record("a"))
    result = canonicalize_chart_aids(source)
    assert canonicalize_chart_aids(source) is result
    assert canonicalize_chart_aids(result) is result
    assert len(empty_catalog_memo) == 1
    # Equal content and alternate source order still prepare their own inputs.
    equal_source = catalog(*source.records)
    assert equal_source == source and equal_source is not source
    assert canonicalize_chart_aids(equal_source) == result
    reverse_source = catalog(*reversed(source.records))
    assert canonicalize_chart_aids(reverse_source) == result
    assert len(empty_catalog_memo) == 3


def test_catalog_identity_memo_is_bounded_and_evicted_inputs_reprepare(empty_catalog_memo):
    sources = [catalog(record(f"aid-{i}", lat=30.0 + i)) for i in range(3)]
    results = [canonicalize_chart_aids(source) for source in sources]
    cache = identity_module._catalog_cache
    assert len(cache) == identity_module._CATALOG_CACHE_LIMIT == 4
    assert all(key == id(pair[0]) for key, pair in cache.items())
    assert all(
        held is not sources[0] and held is not results[0]
        for pair in cache.values()
        for held in pair
    )
    assert len(empty_catalog_memo) == 3
    fresh = canonicalize_chart_aids(sources[0])
    assert fresh == results[0] and fresh is not results[0]
    assert canonicalize_chart_aids(fresh) is fresh
    assert len(empty_catalog_memo) == 4
    assert len(cache) <= identity_module._CATALOG_CACHE_LIMIT


def test_catalog_identity_memo_serializes_first_preparation(empty_catalog_memo):
    source = catalog(record("a"), record("b"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(canonicalize_chart_aids, [source] * 12))
    assert all(result is results[0] for result in results)
    assert len(empty_catalog_memo) == 1


def test_bare_record_iterables_are_not_memoized_and_new_catalog_reflects_edits():
    payload = {"source_sequence": [1]}
    source = record("a", metadata=(("payload", payload),))
    original = catalog(source)
    prepared = canonicalize_chart_aids(original)
    first = canonicalize_chart_records([source])
    payload["source_sequence"].append(2)
    second = canonicalize_chart_records([source])
    # Replacing the catalog is required after changing nested source metadata.
    refreshed = canonicalize_chart_aids(catalog(source))
    assert first != second
    assert refreshed.records == second
    first_components = dict(prepared.records[0].metadata)["source_components"]
    assert dict(first_components[0]["metadata"])["payload"] == {"source_sequence": [1]}


def test_conflict_roots_preserve_only_relevant_ids_across_source_orders():
    rows = [
        record("a", lat=30.0, metadata=(("physical_entity_id", "shared"),)),
        record("b", lat=31.0, metadata=(("physical_entity_id", "shared"),)),
        record("b-extra", lat=31.0, metadata=(("physical_entity_id", "external"),)),
        record("vessel", lat=34.0, kind="vessel", metadata=(("physical_entity_id", "external"),)),
        record("unrelated", lat=32.0, metadata=(("physical_entity_id", "solo"),)),
        record("bare", lat=33.0),
    ]
    expected = canonicalize_chart_records(rows)
    assert expected == canonicalize_chart_records(reversed(rows))
    assert expected == canonicalize_chart_records(rows[2:] + rows[:2])
    by_lat = {row.latitude_deg: dict(row.metadata) for row in expected if is_chart_aid(row)}
    for lat in (30.0, 31.0):
        assert by_lat[lat]["cross_coordinate_physical_ids"] == ["external", "shared"]
        assert by_lat[lat]["physical_linkage_status"] == "unresolved"
    assert by_lat[32.0]["physical_entity_id"] == "solo"
    assert "identity_ambiguous" not in by_lat[32.0]
    assert "physical_linkage_status" not in by_lat[32.0]
    assert by_lat[33.0]["position_uncertainty_m"] is None
    assert by_lat[33.0]["position_uncertainty_status"] == "unknown"
    assert "physical_entity_id" not in by_lat[33.0]
    assert "identity_ambiguous" not in by_lat[33.0]
    assert "physical_linkage_status" not in by_lat[33.0]


@pytest.mark.parametrize(
    "layer", ["Fog_Signal_point", "Radio_Station_point", "Retro_Reflector_point"]
)
def test_auxiliary_noaa_singletons_preserve_unknown_type_source_key_and_attributes(layer):
    source = record(
        f"enc-component:Harbor.{layer}:source=7",
        kind="unknown",
        normalized_type="unknown",
        metadata=(("source_attribute", {"name": "reference fixture"}),),
        provenance=(("source_layer", f"Harbor.{layer}"), ("feature_id", 7)),
    )
    assert is_chart_aid(source)
    output = canonicalize_chart_records([source])
    assert len(output) == 1 and output[0].key == source.key
    assert output[0].normalized_type == "unknown"
    assert dict(output[0].provenance)["source_layer"] == f"Harbor.{layer}"
    metadata = dict(output[0].metadata)
    assert metadata["source_attribute"] == {"name": "reference fixture"}
    assert metadata["source_components"] == [json_value(asdict(source))]
    assert metadata["position_uncertainty_m"] is None
    assert "physical_entity_id" not in metadata
    assert "identity_ambiguous" not in metadata
    assert output == canonicalize_chart_records(output)


def test_retro_reflector_and_beacon_collapse_to_one_known_physical_aid():
    retro = record(
        "a-retro",
        kind="unknown",
        provenance=(("source_layer", "Harbor.Retro_Reflector_point"),),
    )
    beacon = record("b-beacon", kind="beacon", metadata=(("source_attribute", "lateral"),))
    output = canonicalize_chart_records([retro, beacon])
    assert chart_coordinate_groups([retro, beacon]) == ((0, 1),)
    assert len(output) == 1
    assert output[0].normalized_type == "physical_aid"
    assert output[0].key == canonical_aid_key(retro.latitude_deg, retro.longitude_deg)
    assert dict(output[0].metadata)["source_components"] == [
        json_value(asdict(retro)),
        json_value(asdict(beacon)),
    ]
    assert output == canonicalize_chart_records([beacon, retro])
    assert output == canonicalize_chart_records(output)


def test_auxiliary_only_group_remains_unknown_and_source_key_only_group_is_idempotent():
    sources = [
        record(f"enc-component:Harbor.{layer}:source={i}", kind="unknown")
        for i, layer in enumerate(
            ["Fog_Signal_point", "Radio_Station_point", "Retro_Reflector_point"]
        )
    ]
    unrelated = record("unrelated", kind="unknown", metadata=(("description", "radio"),))
    output = canonicalize_chart_records([*sources, unrelated])
    assert len(output) == 2
    assert output[0] is unrelated
    canonical = output[1]
    assert is_chart_aid(canonical)
    assert canonical.normalized_type == "unknown"
    assert canonical.key == canonical_aid_key(32.7, -79.9)
    assert dict(canonical.metadata)["chart_coordinate_component_type"] == "unknown"
    assert dict(canonical.metadata)["source_component_count"] == 3
    assert dict(canonical.metadata)["source_components"] == [
        json_value(asdict(source)) for source in sorted(sources, key=lambda row: row.key)
    ]
    assert output == canonicalize_chart_records(output)
    assert output == canonicalize_chart_records([unrelated, *reversed(sources)])


def test_auxiliary_grouping_uses_normalized_coordinate_without_promoting_other_rows():
    sources = [
        record("fog", kind="unknown", provenance=(("source_layer", "Harbor.Fog_Signal_point"),)),
        record(
            "radio", kind="unknown", metadata=(("service_layer", "Coastal.Radio_Station_point"),)
        ),
        record(
            "retro",
            kind="unknown",
            provenance=(("source_layers", ["Overview.Retro_Reflector_point"]),),
        ),
        record("light", kind="light"),
    ]
    near = record(
        "near-retro",
        lat=32.7 + 1e-5,
        kind="unknown",
        metadata=(("component_layer", "Harbor.Retro_Reflector_point"),),
    )
    vessel = record(
        "vessel", kind="vessel", provenance=(("source_layer", "Harbor.Radio_Station_point"),)
    )
    virtual = record(
        "virtual", kind="virtual_aid", metadata=(("service_layer", "Harbor.Fog_Signal_point"),)
    )
    uncharted = record(
        "uncharted", kind="unknown", metadata=(("service_layer", "Unrelated.Radio_Point"),)
    )
    output = canonicalize_chart_records([*sources, near, vessel, virtual, uncharted])
    assert len(output) == 5
    by_key = {row.key: row for row in output}
    for row in (vessel, virtual, uncharted):
        assert by_key[row.key] is row and not is_chart_aid(row)
    assert by_key["near-retro"].normalized_type == "unknown"
    merged = by_key[canonical_aid_key(32.7, -79.9)]
    assert merged.normalized_type == "physical_aid"
    assert dict(merged.metadata)["source_component_count"] == 4
    assert output == canonicalize_chart_records(
        reversed([*sources, near, vessel, virtual, uncharted])
    )


def test_projection_roundoff_from_portland_normalizes_to_one_lossless_identity():
    first = record("beacon", lat=43.711988900000506, lon=-70.18813330000012)
    second = record("light", lat=43.711988899999994, lon=-70.1881333)
    assert COORDINATE_DECIMAL_PLACES == 10
    assert canonical_aid_key(first.latitude_deg, first.longitude_deg) == canonical_aid_key(
        second.latitude_deg, second.longitude_deg
    )
    (merged,) = canonicalize_chart_records([first, second])
    assert (merged.latitude_deg, merged.longitude_deg) == (43.7119889, -70.1881333)
    metadata = dict(merged.metadata)
    assert metadata["coordinate_decimal_places"] == 10
    assert metadata["source_components"] == [json_value(asdict(first)), json_value(asdict(second))]
    assert merged == canonicalize_chart_records([second, first])[0]
    assert (merged,) == canonicalize_chart_records([merged])


def test_adjacent_floats_merge_but_different_decimal_coordinates_remain_distinct():
    one = record("a")
    roundoff = record("b", lat=nextafter(one.latitude_deg, float("inf")))
    other_lat = record("c", lat=one.latitude_deg + 1e-5)
    other_lon = record("d", lon=one.longitude_deg + 1e-5)
    assert chart_coordinate_groups([one, roundoff, other_lat, other_lon]) == ((0, 1), (3,), (2,))
    assert len(canonicalize_chart_records([one, roundoff, other_lat, other_lon])) == 3
    assert normalize_aid_coordinate(-0.0, 0.0) == (0.0, 0.0)


@pytest.mark.parametrize(
    "lat,lon", [(float("nan"), 0), (0, float("inf")), (91, 0), (0, -181), (True, 0)]
)
def test_coordinate_normalization_retains_validation(lat, lon):
    with pytest.raises(ValueError):
        normalize_aid_coordinate(lat, lon)


def test_previous_rule_wrappers_are_flattened_during_normalization_upgrade():
    sources = [
        record("a", lat=43.711988900000506, lon=-70.18813330000012),
        record("b", lat=43.711988899999994, lon=-70.1881333),
    ]
    wrappers = []
    for source in sources:
        wrappers.append(
            replace(
                source,
                key="old-" + source.key,
                metadata=(
                    ("identity_resolution", "user_exact_coordinate_rule_v1"),
                    ("source_components_schema", COMPONENT_SCHEMA),
                    ("source_components", [json_value(asdict(source))]),
                ),
            )
        )
    (merged,) = canonicalize_chart_records(wrappers)
    assert dict(merged.metadata)["source_components"] == [json_value(asdict(s)) for s in sources]
    assert (merged,) == canonicalize_chart_records([merged])


def test_previous_auxiliary_only_wrapper_keeps_eligibility_and_original_sources():
    source = record(
        "fog", kind="unknown", provenance=(("source_layer", "Harbor.Fog_Signal_point"),)
    )
    (canonical,) = canonicalize_chart_records([source])
    metadata = dict(canonical.metadata)
    metadata["identity_resolution"] = "user_exact_coordinate_rule_v1"
    old = replace(
        canonical, key="chart-coordinate:old", provenance=(), metadata=tuple(metadata.items())
    )
    assert is_chart_aid(old)
    (upgraded,) = canonicalize_chart_records([old])
    assert upgraded.normalized_type == "unknown"
    assert dict(upgraded.metadata)["source_components"] == [json_value(asdict(source))]


def test_roundoff_in_shared_identity_does_not_create_cross_location_ambiguity():
    sources = [
        record("a", metadata=(("physical_entity_id", "same-aid"),)),
        record(
            "b", lat=nextafter(32.7, float("inf")), metadata=(("physical_entity_id", "same-aid"),)
        ),
    ]
    (merged,) = canonicalize_chart_records(sources)
    assert dict(merged.metadata)["physical_linkage_status"] == "resolved"
    assert not dict(merged.metadata)["identity_ambiguous"]


def test_twenty_centimeter_radius_merges_across_decimal_grid_boundaries():
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    lon, lat, _ = geod.fwd(-79.9, 32.7, 90, 0.19)
    first, second = record("a"), record("b", lat=lat, lon=lon)
    assert COORDINATE_MERGE_RADIUS_M == 0.20
    assert canonical_aid_key(first.latitude_deg, first.longitude_deg) != canonical_aid_key(lat, lon)
    output = canonicalize_chart_records([first, second])
    assert len(output) == 1
    assert dict(output[0].metadata)["coordinate_merge_radius_m"] == 0.20
    assert output == canonicalize_chart_records([second, first])
    assert output == canonicalize_chart_records(output)


def test_twenty_centimeter_radius_does_not_chain_merge_more_distant_aids():
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    sources = []
    for key, distance in (("a", 0), ("b", 0.15), ("c", 0.30)):
        lon, lat, _ = geod.fwd(-79.9, 32.7, 0, distance)
        sources.append(record(key, lat=lat, lon=lon))
    output = canonicalize_chart_records(sources)
    assert len(output) == 2
    assert output == canonicalize_chart_records(reversed(sources))
    assert output == canonicalize_chart_records(output)
    for row in output:
        components = dict(row.metadata)["source_components"]
        for a in components:
            for b in components:
                assert (
                    geod.inv(
                        a["longitude_deg"], a["latitude_deg"], b["longitude_deg"], b["latitude_deg"]
                    )[2]
                    <= 0.20
                )


def test_twenty_centimeter_radius_keeps_twenty_one_centimeter_separation():
    from pyproj import Geod

    lon, lat, _ = Geod(ellps="WGS84").fwd(-79.9, 32.7, 0, 0.21)
    assert len(canonicalize_chart_records([record("a"), record("b", lat=lat, lon=lon)])) == 2


def test_twenty_centimeter_radius_handles_longitude_wrap():
    from pyproj import Geod

    lon, lat, _ = Geod(ellps="WGS84").fwd(179.9999999, 32.7, 90, 0.19)
    assert lon < 0
    assert (
        len(
            canonicalize_chart_records(
                [record("a", lat=32.7, lon=179.9999999), record("b", lat=lat, lon=lon)]
            )
        )
        == 1
    )
