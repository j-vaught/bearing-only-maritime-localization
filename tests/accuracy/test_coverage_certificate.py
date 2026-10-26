"""Fail-closed candidate coverage certificates."""

from __future__ import annotations

from pyproj import Transformer
from shapely import to_geojson
from shapely.geometry import Polygon, box

from maritime.geometry.coverage_certificate import (
    CandidateCoverageCertificate,
    certify_candidate_coverage,
    prepare_candidate_coverage_context,
)
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import MapV2

CRS = "EPSG:32618"


def map_v2(
    coverage: Polygon,
    *,
    land: Polygon | None = None,
    unknown: Polygon | None = None,
    crs: str = CRS,
    coverage_status: str | None = None,
    map_version: str = "test-v1",
) -> MapV2:
    status = coverage_status or ("partial" if unknown is not None else "known")
    return MapV2(
        crs=crs,
        source="certificate-test",
        map_version=map_version,
        coverage_status=status,
        coverage_geometry_geojson=to_geojson(coverage),
        unknown_geometry_geojson=to_geojson(unknown) if unknown is not None else None,
        land_geometry_geojson=to_geojson(land) if land is not None else None,
    )


def test_certificate_binds_candidate_all_sorted_target_keys_and_map_identity() -> None:
    source = map_v2(box(-10.0, -10.0, 10.0, 10.0))
    candidate = (0.0, 0.0)
    targets = [("zeta", (4.0, 3.0)), ("alpha", (5.0, -2.0))]
    context = prepare_candidate_coverage_context(source)
    assert context is not None
    certificate = certify_candidate_coverage(context, candidate, targets)

    assert isinstance(certificate, CandidateCoverageCertificate)
    assert certificate.target_map_xy == (("alpha", (5.0, -2.0)), ("zeta", (4.0, 3.0)))
    assert certificate.candidate_water_status == "water"
    assert certificate.proof_method == "convex_hull_inside_known_coverage"
    assert certificate.covers(candidate, targets)
    assert certificate.matches_map(context)
    other_context = prepare_candidate_coverage_context(
        map_v2(box(-10.0, -10.0, 10.0, 10.0), map_version="other")
    )
    assert other_context is not None
    assert not certificate.matches_map(other_context)
    assert not certificate.covers((0.01, 0.0), targets)
    assert not certificate.covers(candidate, [("alpha", (5.0, -2.0))])
    assert not certificate.covers(candidate, [("alpha", (5.0, -2.0)), ("zeta", (4.0, -2.1))])
    assert not certificate.covers(candidate, [("wrong-key", (4.0, 3.0)), ("alpha", (5.0, -2.0))])

    changed_map = map_v2(box(-10.0, -10.0, 10.0, 10.0), map_version="test-v2")
    changed_context = prepare_candidate_coverage_context(changed_map)
    assert changed_context is not None
    changed_certificate = certify_candidate_coverage(changed_context, candidate, targets)
    assert changed_certificate is not None
    assert changed_certificate.map_identity_sha256 != certificate.map_identity_sha256


def test_known_water_candidate_required_and_land_is_not_relabelled_unknown() -> None:
    source = map_v2(box(-10.0, -10.0, 10.0, 10.0), land=box(1.0, -1.0, 3.0, 1.0))

    assert certify_candidate_coverage(source, (0.0, 0.0), [("boat", (5.0, 0.0))]) is not None
    assert certify_candidate_coverage(source, (2.0, 0.0), [("boat", (5.0, 0.0))]) is None
    assert certify_candidate_coverage(source, (11.0, 0.0), []) is None


def test_shoreline_observer_can_certify_known_water_separately_from_target_endpoint() -> None:
    source = map_v2(box(-10.0, -10.0, 10.0, 10.0), land=box(2.0, -1.0, 4.0, 1.0))
    certificate = certify_candidate_coverage(source, (2.0, 0.0), [("shore-target", (4.0, 0.0))])

    assert certificate is not None
    assert certificate.candidate_water_status == "water"
    assert certificate.covers((2.0, 0.0), [("shore-target", (4.0, 0.0))])


def test_both_known_endpoints_do_not_certify_ray_through_unknown_gap() -> None:
    source = map_v2(
        box(0.0, 0.0, 10.0, 10.0),
        unknown=box(4.0, 4.0, 6.0, 6.0),
    )
    candidate = (1.0, 5.0)
    target = ("catalog-1", (9.0, 5.0))

    assert certify_candidate_coverage(source, candidate, [target]) is None


def test_coverage_hole_and_unknown_overlap_are_preserved() -> None:
    coverage_with_hole = Polygon(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        holes=[[(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]],
    )
    hole_map = map_v2(coverage_with_hole)
    unknown_map = map_v2(
        box(0.0, 0.0, 10.0, 10.0),
        unknown=box(4.0, 4.0, 6.0, 6.0),
    )

    for source in (hole_map, unknown_map):
        assert certify_candidate_coverage(source, (1.0, 5.0), [("far-bank", (9.0, 5.0))]) is None
        # Both endpoints lie in covered water; the interior gap still matters.
        certificate = certify_candidate_coverage(source, (1.0, 2.0), [("near-bank", (3.0, 2.0))])
        assert certificate is not None


def test_convex_envelope_is_conservative_for_separate_known_rays() -> None:
    coverage = box(-10.0, -10.0, 10.0, 10.0)
    unknown = box(-0.5, -0.5, 0.5, 0.5)
    source = map_v2(coverage, unknown=unknown)
    candidate = (-4.0, 0.0)
    targets = [("upper", (4.0, 4.0)), ("lower", (4.0, -4.0))]

    # Each segment avoids the unknown center, but their common convex hull covers it.
    assert certify_candidate_coverage(source, candidate, targets) is None


def test_empty_target_set_still_proves_known_water_candidate() -> None:
    source = map_v2(box(-2.0, -2.0, 2.0, 2.0))
    certificate = certify_candidate_coverage(source, (0.0, 0.0), [])

    assert certificate is not None
    assert certificate.target_map_xy == ()
    assert certificate.covers((0.0, 0.0), [])


def test_unknown_coverage_invalid_crs_and_bad_target_sets_fail_closed() -> None:
    unknown_map = MapV2(
        crs=CRS,
        source="certificate-test",
        map_version="unknown-v1",
        coverage_status="unknown",
    )
    geographic_map = map_v2(box(-2.0, -2.0, 2.0, 2.0), crs="EPSG:4326")

    assert certify_candidate_coverage(unknown_map, (0.0, 0.0), []) is None
    assert certify_candidate_coverage(geographic_map, (0.0, 0.0), []) is None
    assert (
        certify_candidate_coverage(
            map_v2(box(-2.0, -2.0, 2.0, 2.0)),
            (0.0, 0.0),
            [("dup", (1.0, 1.0)), ("dup", (2.0, 2.0))],
        )
        is None
    )
    assert (
        certify_candidate_coverage(map_v2(box(-2.0, -2.0, 2.0, 2.0)), (float("nan"), 0.0), [])
        is None
    )


def test_unknown_region_outside_candidate_hull_does_not_invalidate_coverage() -> None:
    source = map_v2(
        box(0.0, 0.0, 10.0, 10.0),
        unknown=box(8.0, 8.0, 9.0, 9.0),
    )
    candidate = (1.0, 1.0)
    targets = [("nearby", (3.0, 2.0))]
    certificate = certify_candidate_coverage(source, candidate, targets)

    assert certificate is not None
    assert certificate.covers(candidate, targets)


def test_certificate_is_frozen_after_proof_minting() -> None:
    source = map_v2(box(-2.0, -2.0, 2.0, 2.0))
    certificate = certify_candidate_coverage(source, (0.0, 0.0), [("one", (1.0, 0.0))])

    assert certificate is not None
    try:
        setattr(certificate, "candidate_xy_m", (1.0, 1.0))
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("Certificate fields must be immutable.")
    assert certificate.covers((0.0, 0.0), [("one", (1.0, 0.0))])


def test_geographic_map_projects_once_and_matches_vector_map_queries() -> None:
    source_coverage = Polygon(
        [(-75.02, 37.98), (-74.98, 37.98), (-74.98, 38.02), (-75.02, 38.02)],
        holes=[[(-75.006, 37.994), (-74.994, 37.994), (-74.994, 38.006), (-75.006, 38.006)]],
    )
    source_unknown = box(-75.004, 38.008, -74.996, 38.016)
    source_land = box(-75.018, 37.99, -75.012, 38.01)
    source = map_v2(
        source_coverage,
        land=source_land,
        unknown=source_unknown,
        crs="EPSG:4326",
        coverage_status="partial",
    )
    context = prepare_candidate_coverage_context(source, target_crs="EPSG:32618")
    vector_map = VectorMap.from_map_v2(source, target_crs="EPSG:32618")
    assert context is not None
    assert context.map_source_crs == "EPSG:4326"
    assert context.map_crs == "EPSG:32618"

    project = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform
    candidates_geo = [
        (-75.015, 38.015),  # covered water
        (-75.015, 38.0),  # land interior
        (-75.0, 38.0),  # coverage hole
        (-75.0, 38.012),  # explicit unknown
        (-75.03, 38.0),  # outside coverage
    ]
    expected = ["water", "land", "unknown", "unknown", "unknown"]
    for coordinate, expected_status in zip(candidates_geo, expected, strict=True):
        xy_m = project(*coordinate)
        assert vector_map.point_water_status(xy_m) == expected_status
        targets = [("target", (xy_m[0] + 10.0, xy_m[1]))]
        certificate = certify_candidate_coverage(context, xy_m, targets)
        if expected_status == "water":
            assert certificate is not None
            assert certificate.matches_map(context)
        else:
            assert certificate is None

    candidate = project(-75.015, 38.015)
    target = project(-75.01, 38.015)
    certificate = certify_candidate_coverage(context, candidate, [("nearby", target)])
    assert certificate is not None
    assert certificate.map_source_crs == "EPSG:4326"
    assert certificate.map_crs == "EPSG:32618"
    equivalent_context = prepare_candidate_coverage_context(source, target_crs=32618)
    assert equivalent_context is not None
    assert certificate.matches_map(equivalent_context)
    other_projection = prepare_candidate_coverage_context(source, target_crs="EPSG:32617")
    assert other_projection is not None
    assert not certificate.matches_map(other_projection)


def test_geographic_map_needs_explicit_projected_metre_target_crs() -> None:
    source = map_v2(box(-75.02, 37.98, -74.98, 38.02), crs="EPSG:4326")

    assert prepare_candidate_coverage_context(source) is None
    assert prepare_candidate_coverage_context(source, target_crs="EPSG:4326") is None
    assert prepare_candidate_coverage_context(source, target_crs="EPSG:32618") is not None
