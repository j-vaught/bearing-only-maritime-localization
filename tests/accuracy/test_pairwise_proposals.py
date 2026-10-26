from __future__ import annotations

from itertools import chain

import pytest
from pyproj import Geod

from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.proposals.pairwise import (
    PairwiseCatalogObject,
    PairwiseObservation,
    generate_geographic_pairwise_proposals,
    generate_pairwise_proposals,
)


def _collect(stream):
    batches = tuple(stream)
    return tuple(chain.from_iterable(batch.proposals for batch in batches)), batches


def test_planar_proposals_are_shuffle_stable_and_batch_finite() -> None:
    observations = (PairwiseObservation(8, 90.0), PairwiseObservation(3, 0.0))
    catalog = (
        PairwiseCatalogObject("east", 100.0, 0.0),
        PairwiseCatalogObject("north", 0.0, 100.0),
    )
    expected, batches = _collect(
        generate_pairwise_proposals(observations, catalog, max_range_m=100.0, batch_size=1)
    )
    shuffled, shuffled_batches = _collect(
        generate_pairwise_proposals(
            tuple(reversed(observations)), tuple(reversed(catalog)), max_range_m=100.0, batch_size=4
        )
    )

    assert len(expected) == 1
    assert expected == shuffled
    assert expected[0].observation_indices == (3, 8)
    assert expected[0].catalog_keys == ("north", "east")
    assert (expected[0].east_m, expected[0].north_m) == pytest.approx((0.0, 0.0))
    assert expected[0].ranges_m == pytest.approx((100.0, 100.0))
    assert batches[-1].generation_status == "completed"
    assert batches[-1].generated_count == 1
    assert shuffled_batches[-1].generation_complete


def test_distinct_keys_at_duplicate_coordinates_remain_distinct_pairs() -> None:
    observations = (PairwiseObservation(0, 45.0), PairwiseObservation(1, 315.0))
    catalog = (
        PairwiseCatalogObject("a", 10.0, 10.0),
        PairwiseCatalogObject("b", 10.0, 10.0),
        PairwiseCatalogObject("c", -10.0, 10.0),
    )
    proposals, batches = _collect(
        generate_pairwise_proposals(observations, catalog, max_range_m=50.0, batch_size=2)
    )

    pair_keys = {proposal.catalog_keys for proposal in proposals}
    assert ("a", "c") in pair_keys
    assert ("b", "c") in pair_keys
    assert all(proposal.proposal_id for proposal in proposals)
    assert batches[-1].generation_status == "completed"


def test_negative_ranges_and_near_parallel_rays_are_rejected() -> None:
    catalog = (
        PairwiseCatalogObject("a", 0.0, 100.0),
        PairwiseCatalogObject("b", 0.0, 200.0),
    )
    away = (PairwiseObservation(0, 0.0), PairwiseObservation(1, 90.0))
    proposals, batches = _collect(generate_pairwise_proposals(away, catalog, max_range_m=200.0))
    assert proposals == ()
    assert batches[-1].generation_status == "completed"

    almost_parallel = (PairwiseObservation(0, 20.0), PairwiseObservation(1, 20.01))
    proposals, _ = _collect(
        generate_pairwise_proposals(
            almost_parallel, catalog, max_range_m=200.0, min_crossing_angle_deg=0.1
        )
    )
    assert proposals == ()


def test_noise_changes_approximate_intersection_without_losing_stable_ids() -> None:
    catalog = (
        PairwiseCatalogObject("east", 100.0, 0.0),
        PairwiseCatalogObject("north", 0.0, 100.0),
    )
    clean, _ = _collect(
        generate_pairwise_proposals(
            (PairwiseObservation(0, 0.0), PairwiseObservation(1, 90.0)),
            catalog,
            max_range_m=150.0,
        )
    )
    noisy, _ = _collect(
        generate_pairwise_proposals(
            (PairwiseObservation(0, 0.5), PairwiseObservation(1, 89.5)),
            catalog,
            max_range_m=150.0,
        )
    )
    assert clean[0].proposal_id == noisy[0].proposal_id
    assert (noisy[0].east_m, noisy[0].north_m) != pytest.approx((clean[0].east_m, clean[0].north_m))


def test_proposal_budget_reports_exhaustion_and_generated_count() -> None:
    observations = (PairwiseObservation(0, 0.0), PairwiseObservation(1, 90.0))
    catalog = (
        PairwiseCatalogObject("a", 100.0, 0.0),
        PairwiseCatalogObject("b", 0.0, 100.0),
        PairwiseCatalogObject("c", 200.0, 0.0),
    )
    proposals, batches = _collect(
        generate_pairwise_proposals(
            observations, catalog, max_range_m=150.0, max_proposals=1, batch_size=1
        )
    )

    assert len(proposals) == 1
    assert batches[-1].generation_status == "budget_exhausted"
    assert batches[-1].generated_count == 1
    assert not batches[-1].generation_complete


def test_geographic_adapter_uses_local_pair_frames_and_marks_approximation() -> None:
    geod = Geod(ellps="WGS84")
    origin = (-76.0, 37.0)
    east = geod.fwd(*origin, 90.0, 800.0)
    north = geod.fwd(*origin, 0.0, 800.0)
    measurements = MeasurementSetV2(
        measurements=(
            MeasurementV2(0.0, "2026-10-25T12:00:00Z"),
            MeasurementV2(90.0, "2026-10-25T12:00:00Z"),
        )
    )
    catalog = CatalogV2(
        records=(
            CatalogRecordV2("east", east[1], east[0]),
            CatalogRecordV2("north", north[1], north[0]),
        ),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="pairwise-geographic",
    )

    proposals, batches = _collect(
        generate_geographic_pairwise_proposals(
            measurements,
            catalog,
            output_origin_lonlat=origin,
            max_range_m=1_000.0,
        )
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.coordinate_model == "local_planar_approximation"
    assert (proposal.east_m, proposal.north_m) == pytest.approx((0.0, 0.0), abs=0.1)
    assert proposal.ranges_m == pytest.approx((800.0, 800.0), abs=0.1)
    assert batches[-1].generation_status == "completed"
