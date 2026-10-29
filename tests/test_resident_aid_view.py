"""CPU checks for the lightweight exact-coordinate aid view over cached maps."""

import json
from dataclasses import replace
from math import nextafter

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import box

from maritime.catalog_identity import canonical_aid_key
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident import aid_view
from maritime.search.gpu.resident.preparation import prepare_world
from maritime.search.prediction import predict_visible_groups
from maritime.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
)
from maritime.simulator.runner import run_simulator_v2

LAT, LON = 32.77, -79.905
XY = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(LON, LAT)
BASE_KEY = "a" * 64


def _record(key, kind="physical_aid", *, lat=LAT, lon=LON, bound=0.0, extent=0.0, **metadata):
    values = {"angular_half_width_deg": extent, **metadata}
    if bound is not None:
        values["position_uncertainty_m"] = bound
    return CatalogRecordV2(key, lat, lon, raw_type=kind, metadata=tuple(values.items()))


def _catalog(*records):
    return CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "fixture", "1", "aid-view")


def _world(catalog, **kwargs):
    x, y = XY
    vm = VectorMap(
        box(x - 300, y - 300, x - 250, y - 250),
        crs="EPSG:32618",
        coverage_geometry=box(x - 1000, y - 1000, x + 1000, y + 1000),
    )
    return prepare_world(
        vm, catalog, identities_certified=True, nominal_offsets_asserted=True, **kwargs
    )


def _row(world, key):
    return world.objects[world.object_keys.index(key)]


def test_exact_location_merge_preserves_map_vessels_and_nearby_aids(monkeypatch):
    catalog = _catalog(
        _record("beacon", bound=2, extent=0.2),
        _record("daymark", "daymark", bound=4, extent=0.7),
        _record("nearby", lat=nextafter(LAT, float("inf"))),
        _record("ship-a", "vessel"),
        _record("ship-b", "vessel"),
        _record("virtual", "virtual_aid"),
    )
    base = _world(catalog, position_bound_m=3)
    original = base.objects.copy()
    original_tree = base.object_tree
    calls = []
    actual_pack_tree = aid_view.pack_tree

    def pack_once(objects, bounds):
        calls.append(objects)
        return actual_pack_tree(objects, bounds)

    monkeypatch.setattr(aid_view, "pack_tree", pack_once)
    view = aid_view.canonicalize_prepared_world(base, catalog)
    key = canonical_aid_key(LAT, LON)
    assert view.object_keys == tuple(sorted((key, "nearby", "ship-a", "ship-b")))
    np.testing.assert_array_equal(_row(view, key)[4:], (4, 0.7, 1, 1))
    assert _row(base, "daymark")[6] == 0  # Baseline previously treated this as unknown.
    for ship in ("ship-a", "ship-b"):
        np.testing.assert_array_equal(_row(view, ship), _row(base, ship))
    assert len(calls) == 1 and calls[0] is view.objects
    assert view.layers is base.layers and view.barriers is base.barriers
    assert view.object_tree is not original_tree
    assert view.object_tree.items is view.objects
    assert view.position_support_complete and view.identities_complete
    np.testing.assert_array_equal(base.objects, original)
    assert not np.shares_memory(base.objects, view.objects)


@pytest.mark.parametrize(
    "layers, marker",
    [
        (("Harbor.Fog_Signal_point",), None),
        (("Harbor.Radio_Station_point",), None),
        (("Harbor.Retro_Reflector_point",), None),
        (
            (
                "Harbor.Fog_Signal_point",
                "Harbor.Radio_Station_point",
                "Harbor.Retro_Reflector_point",
            ),
            None,
        ),
        (
            (
                "Harbor.Fog_Signal_point",
                "Harbor.Radio_Station_point",
                "Harbor.Retro_Reflector_point",
            ),
            "beacon",
        ),
        (
            (
                "Harbor.Fog_Signal_point",
                "Harbor.Radio_Station_point",
                "Harbor.Retro_Reflector_point",
            ),
            "daymark",
        ),
    ],
)
def test_auxiliary_component_identity_preserves_simulator_and_search_types(layers, marker):
    rows = [
        _record(f"component-{index}", "unknown", source_layer=layer)
        for index, layer in enumerate(layers)
    ]
    if marker is not None:
        rows.append(_record("known-marker", marker))
    catalog = _catalog(*rows)
    base = _world(catalog)
    view = aid_view.canonicalize_prepared_world(base, catalog)
    expected_type = "physical_aid" if marker is not None else "unknown"
    expected_key = canonical_aid_key(LAT, LON) if len(rows) > 1 else rows[0].key
    assert view.object_keys == (expected_key,)
    assert view.objects[0, 6] == (1 if marker is not None else 0)
    assert view.layers is base.layers and view.barriers is base.barriers
    scene = PhysicalSceneV2(catalog)
    assert len(scene.catalog.records) == 1
    assert scene.catalog.records[0].normalized_type == expected_type
    x, y = XY
    vm = VectorMap(
        None, crs="EPSG:32618", coverage_geometry=box(x - 1000, y - 1000, x + 1000, y + 1000)
    )
    observer = KnownWaterObserver(LON, LAT - 0.001)
    observer_xy = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        observer.longitude_deg, observer.latitude_deg
    )
    sensor = SensorConfigurationV2(
        bearing_sigma_deg=0, detection_probability=1, expected_clutter_reports=0
    )
    simulation = run_simulator_v2(scene, observer, vm, sensor, seed=11)
    assert len(simulation.measurements.measurements) == 1
    assert simulation.measurements.measurements[0].object_type == expected_type
    predicted = predict_visible_groups(
        observer_xy, catalog, observer_crs="EPSG:32618", visibility_radius_m=10000, vector_map=vm
    )
    assert len(predicted.groups) == 1
    assert predicted.groups[0].member_keys == view.object_keys
    assert predicted.groups[0].object_type == expected_type


@pytest.mark.parametrize("kind", ["vessel", "virtual_aid", "shoreline"])
def test_canonical_aid_view_rejects_unexpected_non_aid_types(monkeypatch, kind):
    catalog = _catalog(_record("a"))
    base = _world(catalog)
    corrupted = replace(catalog, records=(replace(catalog.records[0], normalized_type=kind),))
    monkeypatch.setattr(aid_view, "canonicalize_chart_aids", lambda _: corrupted)
    with pytest.raises(ValueError, match="physical-aid or unknown"):
        aid_view.canonicalize_prepared_world(base, catalog)


def test_known_uncertainty_aliases_and_caller_bounds_remain_conservative():
    catalog = _catalog(_record("a", bound=1, horizontal_uncertainty_m=8), _record("b", bound=2))
    base = _world(catalog, position_bound_m=4)
    assert _row(base, "a")[4] == 4
    view = aid_view.canonicalize_prepared_world(base, catalog)
    assert view.objects[0, 4] == 8


def test_unknown_member_support_cannot_become_finite():
    catalog = _catalog(_record("a", bound=2), _record("b", bound=None))
    view = aid_view.canonicalize_prepared_world(_world(catalog), catalog)
    assert np.isinf(view.objects[0, 4])
    assert not view.position_support_complete


def test_caller_exact_position_assertion_is_preserved_for_unknown_metadata():
    catalog = _catalog(_record("a", bound=None), _record("b", bound=None))
    base = _world(catalog, exact_positions_asserted=True)
    view = aid_view.canonicalize_prepared_world(base, catalog)
    assert view.objects[0, 4] == 0 and view.position_support_complete


def test_all_source_identity_flags_are_required_without_new_certificate():
    catalog = _catalog(_record("a"), _record("b"))
    base = _world(catalog)
    objects = base.objects.copy()
    objects[0, 7] = 0
    view = aid_view.canonicalize_prepared_world(replace(base, objects=objects), catalog)
    assert view.objects[0, 7] == 0 and not view.identities_complete
    # A stale global summary does not hide the proof carried by all source row flags.
    view = aid_view.canonicalize_prepared_world(replace(base, identities_complete=False), catalog)
    assert view.objects[0, 7] == 1 and view.identities_complete


def test_shared_source_id_at_other_coordinate_retains_identity_ambiguity():
    catalog = _catalog(
        _record("a", physical_entity_id="same"),
        _record("b"),
        _record("elsewhere", lat=LAT + 0.001, physical_entity_id="same"),
    )
    base = _world(catalog)
    # Even an otherwise incorrect/global cached certificate cannot erase known aliases.
    objects = base.objects.copy()
    objects[:, 7] = 1
    view = aid_view.canonicalize_prepared_world(replace(base, objects=objects), catalog)
    assert _row(view, canonical_aid_key(LAT, LON))[7] == 0
    assert _row(view, "elsewhere")[7] == 0
    assert not view.identities_complete


@pytest.mark.parametrize(
    "metadata", [{"identity_ambiguous": True}, {"physical_linkage_status": "unresolved"}]
)
def test_explicit_source_ambiguity_cannot_be_upgraded(metadata):
    catalog = _catalog(_record("a", **metadata))
    base = _world(catalog)
    assert base.objects[0, 7] == 1
    view = aid_view.canonicalize_prepared_world(base, catalog)
    assert view.objects[0, 7] == 0 and not view.identities_complete


def test_shared_id_with_non_aid_at_other_coordinate_stays_ambiguous():
    catalog = _catalog(
        _record("a", physical_entity_id="same"),
        _record("b"),
        _record("vessel", "vessel", lat=LAT + 0.001, physical_entity_id="same"),
    )
    base = _world(catalog)
    objects = base.objects.copy()
    objects[:, 7] = 1
    view = aid_view.canonicalize_prepared_world(replace(base, objects=objects), catalog)
    assert _row(view, canonical_aid_key(LAT, LON))[7] == 0
    np.testing.assert_array_equal(_row(view, "vessel"), objects[base.object_keys.index("vessel")])
    assert not view.identities_complete


def test_calibrated_offset_conflict_is_unbounded_and_uses_chart_anchor():
    catalog = _catalog(
        _record("a", visible_feature_offset_east_m=5, visible_feature_offset_north_m=0),
        _record("b", visible_feature_offset_east_m=10, visible_feature_offset_north_m=0),
    )
    base = _world(catalog)
    assert not np.array_equal(base.objects[0, :4], base.objects[1, :4])
    view = aid_view.canonicalize_prepared_world(base, catalog)
    np.testing.assert_array_equal(view.objects[0, :4], (*XY, LON, LAT))
    assert np.isinf(view.objects[0, 4]) and view.objects[0, 7] == 0
    assert not view.position_support_complete and not view.identities_complete


def test_same_numeric_offset_with_incompatible_quality_is_unbounded():
    catalog = _catalog(
        _record("nominal"),
        _record("calibrated", visible_feature_offset_east_m=0, visible_feature_offset_north_m=0),
    )
    base = _world(catalog)
    np.testing.assert_array_equal(base.objects[0, :4], base.objects[1, :4])
    view = aid_view.canonicalize_prepared_world(base, catalog)
    assert np.isinf(view.objects[0, 4]) and view.objects[0, 7] == 0


@pytest.mark.parametrize("column", [0, 1, 2, 3, 5, 6])
def test_source_alignment_rejects_wrong_coordinates_type_or_extent(column):
    catalog = _catalog(_record("a"))
    base = _world(catalog)
    objects = base.objects.copy()
    objects[0, column] += 0.1 if column != 6 else 1
    with pytest.raises(ValueError, match="mismatch"):
        aid_view.canonicalize_prepared_world(replace(base, objects=objects), catalog)


def test_source_alignment_rejects_wrong_key_and_unsupported_finite_bound():
    catalog = _catalog(_record("a", visible_feature_offset_geometry_unsupported=True))
    base = _world(catalog)
    with pytest.raises(ValueError, match="keys/order"):
        aid_view.canonicalize_prepared_world(replace(base, object_keys=("other",)), catalog)
    objects = base.objects.copy()
    objects[0, 4] = 0
    with pytest.raises(ValueError, match="Unsupported source offset"):
        aid_view.canonicalize_prepared_world(replace(base, objects=objects), catalog)


def test_lightweight_sidecar_hit_reuses_rows_and_all_map_references(tmp_path, monkeypatch):
    catalog = _catalog(_record("a"), _record("b"), _record("vessel", "vessel"))
    base = _world(catalog)
    view, hit, key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    assert not hit
    path = tmp_path / f"aid-view-{key}.npz"
    assert path.stat().st_size < 20000
    with np.load(path, allow_pickle=False) as data:
        assert set(data.files) == {"objects", "object_bounds", "object_children", "header"}

    def fail_build(*args):
        raise AssertionError("Sidecar hits must not rebuild objects or shoreline trees.")

    monkeypatch.setattr(aid_view, "_build_view", fail_build)
    again, hit, same_key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    assert hit and same_key == key
    np.testing.assert_array_equal(again.objects, view.objects)
    np.testing.assert_array_equal(again.object_tree.bounds, view.object_tree.bounds)
    np.testing.assert_array_equal(again.object_tree.children, view.object_tree.children)
    assert again.object_keys == view.object_keys
    assert again.layers is base.layers and again.barriers is base.barriers
    assert again.object_tree.items is again.objects
    assert not tuple(tmp_path.glob("*.pending"))


def test_sidecar_identity_covers_base_cache_source_catalog_and_policy(tmp_path, monkeypatch):
    catalog = _catalog(_record("a"), _record("b"))
    base = _world(catalog)
    _, _, key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    _, hit, other = aid_view.load_or_canonicalize(tmp_path, "b" * 64, base, catalog)
    assert not hit and other != key
    changed = replace(catalog, source="another-provenance-source")
    _, hit, another = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, changed)
    assert not hit and another != key
    monkeypatch.setattr(aid_view, "POLICY_VERSION", "NextExactCoordinatePolicy")
    _, hit, policy_key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    assert not hit and policy_key != key
    with pytest.raises(ValueError, match="SHA-256"):
        aid_view.load_or_canonicalize(tmp_path, "wrong", base, catalog)


def test_corrupt_sidecar_fails_without_rebuilding(tmp_path, monkeypatch):
    catalog = _catalog(_record("a"))
    base = _world(catalog)
    _, _, key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    path = tmp_path / f"aid-view-{key}.npz"
    path.write_bytes(b"corrupt")

    def fail_build(*args):
        raise AssertionError("Corrupt sidecars must fail visibly without rebuilding.")

    monkeypatch.setattr(aid_view, "_build_view", fail_build)
    with pytest.raises(ValueError):
        aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)


def test_modified_payload_and_malformed_tree_fail(tmp_path):
    catalog = _catalog(_record("a"), _record("b", lat=LAT + 0.001))
    base = _world(catalog)
    _, _, key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    path = tmp_path / f"aid-view-{key}.npz"
    with np.load(path, allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    header = json.loads(arrays["header"].tobytes())
    arrays["object_children"][0, 1] = 0
    with path.open("wb") as stream:
        np.savez(stream, **arrays)
    with pytest.raises(ValueError, match="checksum"):
        aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
    payload = {name: array for name, array in arrays.items() if name != "header"}
    header["payload_sha256"] = aid_view._payload_digest(payload, tuple(header["object_keys"]))
    arrays["header"] = np.frombuffer(json.dumps(header).encode(), dtype=np.uint8)
    with path.open("wb") as stream:
        np.savez(stream, **arrays)
    with pytest.raises(ValueError, match="descendants"):
        aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)


def test_empty_catalog_and_singleton_sidecar_round_trips(tmp_path):
    for records in ((), (_record("daymark", "daymark"),)):
        catalog = _catalog(*records)
        base = _world(catalog)
        view, hit, key = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
        assert not hit and len(view.objects) == len(records)
        again, hit, _ = aid_view.load_or_canonicalize(tmp_path, BASE_KEY, base, catalog)
        assert hit and again.objects.shape == (len(records), 8)
        assert again.position_support_complete and again.identities_complete
        assert again.layers is base.layers and again.barriers is base.barriers
        assert (tmp_path / f"aid-view-{key}.npz").exists()
        if records:
            assert again.object_keys == ("daymark",) and again.objects[0, 6] == 1
