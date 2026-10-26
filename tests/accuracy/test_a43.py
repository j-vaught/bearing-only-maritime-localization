from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_bounded_profile_writes_partial_artifacts_on_timeout(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    output = tmp_path / "profile"
    command = [
        sys.executable,
        str(repository / "tools" / "profile_pairwise_feasibility.py"),
        "--seconds",
        "0.05",
        "--profile-output",
        str(output),
        "--",
        "module",
        "timeit",
        "-n",
        "100000000",
        "pass",
    ]
    result = subprocess.run(command, capture_output=True, check=False, timeout=5)

    assert result.returncode == 124
    assert output.with_suffix(".prof").is_file()
    assert output.with_suffix(".txt").is_file()
    metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["status"] == "watchdog_timeout"
    assert metadata["elapsed_s"] < 2.0


def _old_localframe_reference(
    measurements,
    catalog,
    *,
    output_origin_lonlat,
    max_range_m,
    batch_size=1_024,
    max_proposals=None,
    min_crossing_angle_deg=0.1,
):
    """Independent pre-optimization stream using LocalFrame for every pair."""
    from itertools import combinations
    from math import radians

    import numpy as np
    from pyproj import Geod

    from bearing_localization.search.proposals.pairwise import (
        PairwiseObservation,
        PairwiseProposal,
        PairwiseProposalBatch,
        _proposal_id,
    )
    from maritime_localization_shared.geospatial.coordinates import LocalFrame
    from maritime_localization_shared.geospatial.spatial_index import GeographicSpatialIndex

    ordered_measurements = sorted(
        measurements.measurements,
        key=lambda item: (
            item.bearing_deg,
            item.timestamp_utc,
            item.object_type or "",
            item.sigma_deg if item.sigma_deg is not None else -1.0,
            item.angular_half_width_deg if item.angular_half_width_deg is not None else -1.0,
        ),
    )
    observations = tuple(
        PairwiseObservation(index, item.bearing_deg)
        for index, item in enumerate(ordered_measurements)
    )
    records = tuple(sorted(catalog.records, key=lambda item: item.key))
    index = GeographicSpatialIndex(
        [(record.longitude_deg, record.latitude_deg) for record in records],
        keys=[record.key for record in records],
    )
    geod = Geod(ellps="WGS84")
    output_frame = LocalFrame(output_origin_lonlat)
    minimum_sine = np.sin(np.deg2rad(min_crossing_angle_deg))
    pending = []
    generated = 0
    stopped = False
    batches = []

    for observation_i, observation_j in combinations(observations, 2):
        oi, oj = observation_i.report_index, observation_j.report_index
        ui = np.asarray(
            [np.sin(radians(observation_i.bearing_deg)), np.cos(radians(observation_i.bearing_deg))]
        )
        uj = np.asarray(
            [np.sin(radians(observation_j.bearing_deg)), np.cos(radians(observation_j.bearing_deg))]
        )
        matrix = np.column_stack((ui, -uj))
        if abs(float(np.linalg.det(matrix))) < minimum_sine:
            continue
        for source in records:
            nearby_keys = index.query_radius_keys(
                source.longitude_deg, source.latitude_deg, 2.0 * max_range_m
            )
            for target_key in nearby_keys:
                if str(target_key) <= source.key:
                    continue
                target = records[index.index_for_key(target_key)]
                midpoint_bearing, _, separation = geod.inv(
                    source.longitude_deg,
                    source.latitude_deg,
                    target.longitude_deg,
                    target.latitude_deg,
                )
                if separation > 2.0 * max_range_m:
                    continue
                midpoint_lon, midpoint_lat, _ = geod.fwd(
                    source.longitude_deg,
                    source.latitude_deg,
                    midpoint_bearing,
                    separation / 2.0,
                )
                pair_frame = LocalFrame((midpoint_lon, midpoint_lat))
                for assigned_i, assigned_j in ((source, target), (target, source)):
                    source_xy = pair_frame.to_local(
                        (assigned_i.longitude_deg, assigned_i.latitude_deg)
                    )
                    target_xy = pair_frame.to_local(
                        (assigned_j.longitude_deg, assigned_j.latitude_deg)
                    )
                    delta = np.asarray(source_xy) - np.asarray(target_xy)
                    try:
                        range_i, range_j = np.linalg.solve(matrix, delta)
                    except np.linalg.LinAlgError:
                        continue
                    if range_i <= 0.0 or range_j <= 0.0:
                        continue
                    candidate_local = np.asarray(source_xy) - range_i * ui
                    candidate_lonlat = pair_frame.to_geographic(*candidate_local)
                    candidate_to_i = geod.inv(
                        candidate_lonlat[0],
                        candidate_lonlat[1],
                        assigned_i.longitude_deg,
                        assigned_i.latitude_deg,
                    )[2]
                    candidate_to_j = geod.inv(
                        candidate_lonlat[0],
                        candidate_lonlat[1],
                        assigned_j.longitude_deg,
                        assigned_j.latitude_deg,
                    )[2]
                    if candidate_to_i > max_range_m or candidate_to_j > max_range_m:
                        continue
                    if max_proposals is not None and generated >= max_proposals:
                        stopped = True
                        break
                    east_m, north_m = output_frame.to_local(candidate_lonlat)
                    key_pair = (assigned_i.key, assigned_j.key)
                    pending.append(
                        PairwiseProposal(
                            proposal_id=_proposal_id((oi, oj), key_pair),
                            observation_indices=(oi, oj),
                            catalog_keys=key_pair,
                            east_m=east_m,
                            north_m=north_m,
                            ranges_m=(candidate_to_i, candidate_to_j),
                            coordinate_model="local_planar_approximation",
                        )
                    )
                    generated += 1
                    if len(pending) >= batch_size:
                        batches.append(
                            PairwiseProposalBatch(
                                tuple(pending), generated, "generation_incomplete"
                            )
                        )
                        pending.clear()
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            break

    status = "budget_exhausted" if stopped else "completed"
    batches.append(PairwiseProposalBatch(tuple(pending), generated, status))
    return batches


def _assert_stream_matches_localframe_reference(actual, reference, output_origin_lonlat) -> None:
    from pyproj import Geod

    from maritime_localization_shared.geospatial.coordinates import LocalFrame

    assert [batch.generation_status for batch in actual] == [
        batch.generation_status for batch in reference
    ]
    assert [batch.generated_count for batch in actual] == [
        batch.generated_count for batch in reference
    ]
    actual_proposals = [proposal for batch in actual for proposal in batch.proposals]
    reference_proposals = [proposal for batch in reference for proposal in batch.proposals]
    assert len(actual_proposals) == len(reference_proposals)
    output_frame = LocalFrame(output_origin_lonlat)
    geod = Geod(ellps="WGS84")
    for observed, expected in zip(actual_proposals, reference_proposals, strict=True):
        assert observed.proposal_id == expected.proposal_id
        assert observed.observation_indices == expected.observation_indices
        assert observed.catalog_keys == expected.catalog_keys
        actual_geo = output_frame.to_geographic(observed.east_m, observed.north_m)
        expected_geo = output_frame.to_geographic(expected.east_m, expected.north_m)
        assert geod.inv(*actual_geo, *expected_geo)[2] <= 1e-6
        assert observed.ranges_m == pytest.approx(expected.ranges_m, abs=1e-6)
        assert observed.coordinate_model == expected.coordinate_model


def test_geodesic_polar_helpers_match_localframe_oracle() -> None:
    import numpy as np
    import pytest
    from pyproj import Geod

    from bearing_localization.search.proposals.pairwise import (
        _geodesic_polar_local_xy,
        _geodesic_polar_to_geographic,
    )
    from maritime_localization_shared.geospatial.coordinates import LocalFrame

    geod = Geod(ellps="WGS84")
    cases = (
        ((-76.0, 37.0), 45.0, 8_000.0),
        ((179.999, 83.0), 271.0, 18_000.0),
        ((12.0, -84.0), 4.0, 9_000.0),
        ((0.0, 0.0), 179.0, 1_000.0),
    )
    for origin, bearing, separation in cases:
        target_lon, target_lat, _ = geod.fwd(*origin, bearing, separation)
        midpoint_azimuth, _, distance = geod.inv(*origin, target_lon, target_lat)
        midpoint_lon, midpoint_lat, _ = geod.fwd(*origin, midpoint_azimuth, distance / 2.0)
        midpoint = (midpoint_lon, midpoint_lat)
        reference_frame = LocalFrame(midpoint)
        for point in (origin, (target_lon, target_lat)):
            expected_xy = reference_frame.to_local(point)
            actual_xy = _geodesic_polar_local_xy(midpoint, point, geod)
            assert actual_xy == pytest.approx(expected_xy, abs=1e-6)
        for east_m, north_m in ((0.0, 0.0), (1_234.5, -678.9), (-9_000.0, 50.0)):
            expected = reference_frame.to_geographic(east_m, north_m)
            actual = _geodesic_polar_to_geographic(midpoint, east_m, north_m, geod)
            assert geod.inv(*actual, *expected)[2] <= 1e-6
        assert _geodesic_polar_to_geographic(midpoint, 0.0, 0.0, geod) == midpoint
        for bearing_deg in (0.0, 37.0, 90.0, 181.0, 359.9):
            step_lon, step_lat, _ = geod.fwd(*midpoint, bearing_deg, 1.0)
            step_xy = reference_frame.to_local((step_lon, step_lat))
            expected_xy = (
                1.0 * np.sin(np.deg2rad(bearing_deg)),
                1.0 * np.cos(np.deg2rad(bearing_deg)),
            )
            assert step_xy == pytest.approx(expected_xy, abs=1e-6)

    from maritime_localization_shared.geospatial.spatial_index import GeographicSpatialIndex

    boundary_target = geod.fwd(*cases[0][0], 180.0, 2_000.0)
    boundary_distance = geod.inv(*cases[0][0], boundary_target[0], boundary_target[1])[2]
    index = GeographicSpatialIndex(
        (cases[0][0], (boundary_target[0], boundary_target[1])),
        keys=("origin", "at-2r"),
    )
    assert index.query_radius_keys(*cases[0][0], boundary_distance) == ("origin", "at-2r")


def test_geodesic_polar_stream_matches_localframe_reference() -> None:
    from pyproj import Geod

    from bearing_localization.search.proposals import pairwise
    from maritime_localization_shared.contracts.inputs_v2 import (
        CatalogRecordV2,
        CatalogV2,
        MeasurementSetV2,
        MeasurementV2,
    )

    geod = Geod(ellps="WGS84")
    origin = (179.999, 83.0)
    catalog = CatalogV2(
        records=tuple(
            CatalogRecordV2(
                key,
                geod.fwd(*origin, bearing, 500.0)[1],
                geod.fwd(*origin, bearing, 500.0)[0],
            )
            for key, bearing in (
                ("north", 0.0),
                ("east", 90.0),
                ("south", 180.0),
                ("west", 270.0),
                ("duplicate-north", 0.0),
            )
        ),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a43-geodesic-polar-stream",
    )
    measurements = MeasurementSetV2(
        measurements=tuple(
            MeasurementV2(bearing, "2026-10-25T12:00:00Z") for bearing in (0.0, 0.05, 90.0, 135.0)
        )
    )
    for max_range_m in (500.0, 500.001, 1_000.0):
        actual = list(
            pairwise.generate_geographic_pairwise_proposals(
                measurements,
                catalog,
                output_origin_lonlat=origin,
                max_range_m=max_range_m,
                batch_size=3,
            )
        )
        reference = _old_localframe_reference(
            measurements,
            catalog,
            output_origin_lonlat=origin,
            max_range_m=max_range_m,
            batch_size=3,
        )
        _assert_stream_matches_localframe_reference(actual, reference, origin)

    budget_actual = list(
        pairwise.generate_geographic_pairwise_proposals(
            measurements,
            catalog,
            output_origin_lonlat=origin,
            max_range_m=1_000.0,
            batch_size=3,
            max_proposals=2,
        )
    )
    budget_reference = _old_localframe_reference(
        measurements,
        catalog,
        output_origin_lonlat=origin,
        max_range_m=1_000.0,
        batch_size=3,
        max_proposals=2,
    )
    _assert_stream_matches_localframe_reference(budget_actual, budget_reference, origin)


def test_geodesic_polar_falls_back_for_submicron_catalog_separation() -> None:
    from bearing_localization.search.proposals import pairwise
    from maritime_localization_shared.contracts.inputs_v2 import (
        CatalogRecordV2,
        CatalogV2,
        MeasurementSetV2,
        MeasurementV2,
    )

    catalog = CatalogV2(
        records=(
            CatalogRecordV2(
                "daymark",
                41.12593270000002,
                -73.28722240000002,
            ),
            CatalogRecordV2(
                "beacon",
                41.1259327000007,
                -73.28722240000025,
            ),
        ),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a43-submicron-separation",
    )
    measurements = MeasurementSetV2(
        measurements=tuple(
            MeasurementV2(bearing, "2026-10-25T12:00:00Z")
            for bearing in (196.22689126750106, 200.56385249370462)
        )
    )
    actual = list(
        pairwise.generate_geographic_pairwise_proposals(
            measurements,
            catalog,
            output_origin_lonlat=(-73.88006033640767, 35.18212735680013),
            max_range_m=5_000.0,
            batch_size=64,
        )
    )
    reference = _old_localframe_reference(
        measurements,
        catalog,
        output_origin_lonlat=(-73.88006033640767, 35.18212735680013),
        max_range_m=5_000.0,
        batch_size=64,
    )

    _assert_stream_matches_localframe_reference(
        actual,
        reference,
        (-73.88006033640767, 35.18212735680013),
    )
    assert sum(len(batch.proposals) for batch in actual) == 0
