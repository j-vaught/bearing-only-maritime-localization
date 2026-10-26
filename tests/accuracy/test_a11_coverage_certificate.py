"""Fail-closed candidate coverage certificates."""

from __future__ import annotations

from shapely import to_geojson
from shapely.geometry import Polygon, box

from maritime_localization_shared.contracts.inputs_v2 import MapV2
from maritime_localization_shared.geospatial.coverage_certificate import (
    CandidateCoverageCertificate,
    certify_candidate_coverage,
)

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
    certificate = certify_candidate_coverage(source, candidate, targets)

    assert isinstance(certificate, CandidateCoverageCertificate)
    assert certificate.target_map_xy == (("alpha", (5.0, -2.0)), ("zeta", (4.0, 3.0)))
    assert certificate.candidate_water_status == "water"
    assert certificate.proof_method == "convex_hull_inside_known_coverage"
    assert certificate.covers(candidate, targets)
    assert certificate.matches_map(source)
    assert not certificate.matches_map(map_v2(box(-10.0, -10.0, 10.0, 10.0), map_version="other"))
    assert not certificate.covers((0.01, 0.0), targets)
    assert not certificate.covers(candidate, [("alpha", (5.0, -2.0))])
    assert not certificate.covers(candidate, [("alpha", (5.0, -2.0)), ("zeta", (4.0, -2.1))])
    assert not certificate.covers(candidate, [("wrong-key", (4.0, 3.0)), ("alpha", (5.0, -2.0))])

    changed_map = map_v2(box(-10.0, -10.0, 10.0, 10.0), map_version="test-v2")
    changed_certificate = certify_candidate_coverage(changed_map, candidate, targets)
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


def test_certificate_is_frozen_and_malformed_witness_fails_closed() -> None:
    source = map_v2(box(-2.0, -2.0, 2.0, 2.0))
    certificate = certify_candidate_coverage(source, (0.0, 0.0), [("one", (1.0, 0.0))])

    assert certificate is not None
    try:
        object.__setattr__(certificate, "_coverage_wkb", b"invalid WKB")
    except (AttributeError, TypeError):
        pass
    assert not certificate.covers((0.0, 0.0), [("one", (1.0, 0.0))])
