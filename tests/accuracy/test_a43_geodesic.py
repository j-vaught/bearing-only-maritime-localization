from __future__ import annotations

import hashlib
import json
from math import asin, degrees

import numpy as np
import pytest
from pyproj import Geod

from bearing_localization.search.proposals.geodesic_wedge import (
    _cell_residual_and_evidence,
    _cell_residual_and_evidence_batch,
    _neighbor_cell_indices,
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame


def _small_fixture():
    frame = LocalFrame((-76.43, 39.32))
    source_lon, source_lat, _ = Geod(ellps="WGS84").fwd(*frame.origin_lonlat, 215.0, 1_000.0)
    return (
        frame,
        [WedgeObservation(0, 35.0)],
        [GeographicWedgeCatalogObject("source-0", source_lon, source_lat)],
    )


def test_generator_exposes_solver_tolerance_and_observed_residual_metadata():
    frame, observations, catalog = _small_fixture()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
    )

    assert result.solver_tolerance_deg == 1e-7
    assert 0.0 <= result.max_solver_residual_deg <= result.solver_tolerance_deg
    assert result.source_pairs_processed == 1
    assert result.source_pairs_failed == 0


def test_solver_tolerance_is_capped_by_angular_agreement_and_sigma():
    frame, observations, catalog = _small_fixture()
    with pytest.raises(ValueError, match="must not exceed"):
        generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            observation_sigmas_deg={0: 0.01},
            solver_tolerance_deg=0.00011,
        )


@pytest.mark.parametrize("value", [0.0, -1e-9, float("nan"), float("inf")])
def test_solver_tolerance_must_be_finite_and_positive(value):
    frame, observations, catalog = _small_fixture()
    with pytest.raises(ValueError, match="finite and positive"):
        generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            solver_tolerance_deg=value,
        )


def _categorical_signature(result):
    items = [
        (
            item.candidate_id,
            item.vote_count,
            item.matching_count,
            item.witness_keys,
            item.witness_report_keys,
        )
        for item in result.proposals
    ]
    metadata = (
        result.candidate_cells_total,
        result.source_pairs_total,
        result.source_pairs_processed,
        result.source_pairs_failed,
        result.geodesic_samples_total,
        result.geodesic_samples_failed,
        result.geodesic_samples_degenerate,
        result.generation_status,
        result.budget_reason,
    )
    return {"items": items, "meta": metadata}


def _categorical_hash(result):
    encoded = json.dumps(_categorical_signature(result), separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _small_complete_fixture():
    frame = LocalFrame((0.0, 0.0))
    truth = frame.to_geographic(50.0, 50.0)
    geod = Geod(ellps="WGS84")
    observations = []
    catalog = []
    for index, bearing_deg in enumerate((0.0, 90.0, 180.0, 270.0, 45.0)):
        longitude, latitude, _ = geod.fwd(*truth, bearing_deg, 1_000.0)
        catalog.append(GeographicWedgeCatalogObject(f"true-{index}", longitude, latitude))
        observations.append(WedgeObservation(index, bearing_deg))
    artifact = frame.to_geographic(550.0, 550.0)
    catalog.append(GeographicWedgeCatalogObject("artifact", *artifact))
    return frame, observations, catalog


def _frozen_real_subset():
    # The first 20 sorted source records in the A26 frozen catalog snapshot.
    rows = (
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AA.000:approach.bcnlat.fid=1933863",
            -73.28722240000025,
            41.1259327000007,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AA.000:approach.bcnlat.fid=1933864",
            -73.28727470000013,
            41.121877499999755,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AA.000:approach.bcnlat.fid=1933865",
            -73.22257999999965,
            41.142966699999306,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AB.000:approach.bcnlat.fid=1933866",
            -73.17680029999974,
            41.15483139999924,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AB.000:approach.bcnlat.fid=1933867",
            -73.0930386,
            41.160738099999435,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AB.000:approach.bcnlat.fid=1933868",
            -72.9561092999993,
            41.22564240000065,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AB.000:approach.bcnlat.fid=1933869",
            -73.17981139999938,
            41.156726699999126,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AB.000:approach.bcnlat.fid=1933870",
            -73.10901720000038,
            41.168386699999246,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AC.000:approach.bcnlat.fid=1933871",
            -72.82790469999964,
            41.25631109999995,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AC.000:approach.bcnlat.fid=1933872",
            -72.77083329999914,
            41.262222200000906,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AC.000:approach.bcnlat.fid=1933873",
            -72.71083329999965,
            41.2456388999999,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AD.000:approach.bcnlat.fid=1933874",
            -72.34759660000054,
            41.10368910000034,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AD.000:approach.bcnlat.fid=1933875",
            -72.48515439999937,
            41.25630169999931,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AD.000:approach.bcnlat.fid=1933876",
            -72.47300440000072,
            41.268642799999725,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AE.000:approach.bcnlat.fid=1933877",
            -72.2408333000003,
            41.15080560000024,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AE.000:approach.bcnlat.fid=1933878",
            -72.06008330000077,
            41.31600000000071,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AE.000:approach.bcnlat.fid=1933879",
            -72.24196669999947,
            41.15339999999924,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AE.000:approach.bcnlat.fid=1933880",
            -72.24002780000046,
            41.149694399999134,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AF.000:approach.bcnlat.fid=1933881",
            -71.91310720000001,
            41.32525960000021,
        ),
        (
            "enc-component:approach:Approach.Beacon_Lateral_point:US4CT1AF.000:approach.bcnlat.fid=1933882",
            -71.90792900000088,
            41.31657769999947,
        ),
    )
    observations = tuple(
        WedgeObservation(index, bearing)
        for index, bearing in enumerate(
            (
                196.46968947381453,
                200.36477783567253,
                206.225571474809,
                215.65789052601954,
                194.0279815582051,
            )
        )
    )
    catalog = tuple(GeographicWedgeCatalogObject(*row) for row in rows)
    return observations, catalog


def test_neighbor_cell_splat_matches_scalar_set_order_at_negative_boundaries():
    local_x = np.asarray([-250.0, -0.0001, 0.0, 249.9999, 250.0, 501.0])
    local_y = np.asarray([251.0, 0.0, -250.0, 500.0, -0.0001, -500.0])
    valid = np.asarray([True, True, True, True, True, False])
    actual = _neighbor_cell_indices(local_x, local_y, valid, 250.0)
    expected = set()
    for x, y, is_valid in zip(local_x, local_y, valid, strict=True):
        if not is_valid:
            continue
        base_i = int(np.floor(float(x / 250.0)))
        base_j = int(np.floor(float(y / 250.0)))
        expected.update((base_i + di, base_j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1))
    assert [tuple(row) for row in actual] == sorted(expected)


def test_batched_cell_evidence_matches_scalar_oracle_near_decision_edges():
    geod = Geod(ellps="WGS84")
    source_lon, source_lat = -76.43, 39.32
    cell_size = 100.0
    half_diagonal = cell_size * 2.0**0.5 / 2.0
    final_distance = 1_000.0 + half_diagonal
    distances = (
        0.0,
        half_diagonal - 2e-6,
        half_diagonal,
        half_diagonal + 2e-6,
        500.0,
        final_distance - 2e-6,
        final_distance,
        final_distance + 2e-6,
    )
    for distance in distances:
        lon, lat, _ = geod.fwd(source_lon, source_lat, 15.0, distance)
        inverse_bearing, _, actual_distance = geod.inv(lon, lat, source_lon, source_lat)
        quantization = (
            degrees(asin(min(1.0, half_diagonal / actual_distance)))
            if actual_distance > half_diagonal
            else 0.0
        )
        threshold = 0.5 + quantization
        for residual_offset in (0.0, threshold, threshold + 1e-9):
            desired = (inverse_bearing - residual_offset) % 360.0
            accepted, normalized, evidence = _cell_residual_and_evidence_batch(
                source_lon_deg=source_lon,
                source_lat_deg=source_lat,
                cell_lon_deg=np.asarray([lon]),
                cell_lat_deg=np.asarray([lat]),
                desired_bearing_deg=desired,
                cell_size_m=cell_size,
                angular_tolerance_deg=0.5,
                sigma_deg=0.5,
                final_distance_m=final_distance,
            )
            expected = _cell_residual_and_evidence(
                source_lon_deg=source_lon,
                source_lat_deg=source_lat,
                cell_lon_deg=lon,
                cell_lat_deg=lat,
                desired_bearing_deg=desired,
                cell_size_m=cell_size,
                angular_tolerance_deg=0.5,
                sigma_deg=0.5,
                final_distance_m=final_distance,
            )
            assert bool(accepted[0]) is expected[0]
            assert normalized[0] == pytest.approx(expected[1], rel=0.0, abs=5e-15)
            assert evidence[0] == pytest.approx(expected[2], rel=0.0, abs=5e-15)


def test_complete_small_fixture_matches_scalar_categorical_oracle():
    frame, observations, catalog = _small_complete_fixture()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    assert (
        _categorical_hash(result)
        == "414f342659342ffc5d701ffe9861d9637c5349dd8e3381e6a4c499986e47c1fd"
    )
    assert result.solver_tolerance_deg == 1e-7
    assert result.max_solver_residual_deg <= result.solver_tolerance_deg


def test_deterministic_frozen_catalog_subset_matches_scalar_oracle():
    observations, catalog = _frozen_real_subset()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=LocalFrame((-76.43, 39.32)),
        max_range_m=10_000.0,
        cell_size_m=250.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=100,
        max_candidate_cells=100_000,
        source_chunk_size=16,
    )
    assert (
        _categorical_hash(result)
        == "a49651d883beab245cd8329255e007e7e16b4a207271125d3871d3e765779c77"
    )
    assert result.source_pairs_processed == 100
    assert result.source_pairs_failed == 0
    assert result.geodesic_samples_failed == 0
    assert result.candidate_cells_total == 4_511
