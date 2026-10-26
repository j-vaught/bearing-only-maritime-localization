from __future__ import annotations

from dataclasses import replace

import pytest
from pyproj import Geod

import bearing_localization.search.proposals.witness_seeds as witness_seeds
from bearing_localization.search.proposals.geodesic_wedge import GeodesicWedgeProposal
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)
from bearing_localization.search.proposals.witness_seeds import (
    generate_continuous_witness_seeds,
)
from maritime_localization_shared.geospatial.coordinates import (
    LocalFrame,
    geodesic_bearing_distance,
)

_GEOD = Geod(ellps="WGS84")
_TRUTH = (-75.5, 37.0)


def _target(key: str, bearing_deg: float, range_m: float, origin=_TRUTH):
    lon, lat, _ = _GEOD.fwd(origin[0], origin[1], bearing_deg, range_m)
    return GeographicWedgeCatalogObject(key, lon, lat)


def _proposal(
    witness_report_keys: tuple[tuple[int, tuple[str, ...]], ...],
    *,
    cell_i: int = 0,
    cell_j: int = 0,
    candidate_id: str | None = None,
    cell_size_m: float = 250.0,
) -> GeodesicWedgeProposal:
    return GeodesicWedgeProposal(
        candidate_id=candidate_id or f"cell:{cell_i}:{cell_j}",
        cell_i=cell_i,
        cell_j=cell_j,
        east_m=(cell_i + 0.5) * cell_size_m,
        north_m=(cell_j + 0.5) * cell_size_m,
        vote_count=len(witness_report_keys),
        matching_count=len(witness_report_keys),
        witness_keys=tuple(sorted({key for _, keys in witness_report_keys for key in keys})),
        witness_report_keys=witness_report_keys,
        normalized_angular_residual=0.0,
        evidence_score=1.0,
    )


def _two_report_fixture(bearings=(359.8, 90.2), ranges=(1_500.0, 2_000.0)):
    frame_origin = _GEOD.fwd(_TRUTH[0], _TRUTH[1], 225.0, 150.0)[:2]
    frame = LocalFrame(frame_origin)
    observations = (
        WedgeObservation(10, bearings[0]),
        WedgeObservation(20, bearings[1]),
    )
    catalog = (
        _target("source-a", bearings[0], ranges[0]),
        _target("source-b", bearings[1], ranges[1]),
    )
    proposal = _proposal(((10, ("source-a",)), (20, ("source-b",))))
    return frame, observations, catalog, proposal


def _generate(frame, observations, catalog, proposal, **kwargs):
    return generate_continuous_witness_seeds(
        observations,
        catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=5_000.0,
        max_pair_evaluations=100,
        max_candidate_seeds=100,
        **kwargs,
    )


def test_seed_refines_a_land_cell_center_to_the_independent_water_truth() -> None:
    frame, observations, catalog, proposal = _two_report_fixture()
    truth_en = frame.to_local(_TRUTH)
    cell_center = (proposal.east_m, proposal.north_m)

    # Synthetic test-only shoreline: the cell center is land, the independent
    # simulator truth and its recovered seed are water.
    def is_water(east: float, north: float) -> bool:
        return (east - cell_center[0]) ** 2 + (north - cell_center[1]) ** 2 > 5.0**2

    assert not is_water(*cell_center)
    assert is_water(*truth_en)

    result = _generate(frame, observations, catalog, proposal)

    assert result.generation_status == "completed"
    assert result.candidate_seeds_generated == 1
    assert (
        result.pairs_considered
        == result.pairs_accepted + result.pairs_rejected + result.pairs_failed
    )
    seed = result.seeds[0]
    assert (seed.east_m, seed.north_m) == pytest.approx(truth_en, abs=0.01)
    assert is_water(seed.east_m, seed.north_m)
    assert proposal.cell_i * 250.0 <= seed.east_m <= (proposal.cell_i + 1) * 250.0
    assert proposal.cell_j * 250.0 <= seed.north_m <= (proposal.cell_j + 1) * 250.0
    assert max(abs(value) for value in seed.bearing_residuals_deg) < 1e-7
    for bearing, key in zip((359.8, 90.2), ("source-a", "source-b"), strict=True):
        target = next(item for item in catalog if item.key == key)
        independent = geodesic_bearing_distance(
            (seed.longitude_deg, seed.latitude_deg),
            (target.longitude_deg, target.latitude_deg),
        )
        assert independent.true_bearing_deg == pytest.approx(bearing, abs=1e-7)


def test_source_pair_constraints_count_shared_or_coincident_catalog_sources() -> None:
    frame, observations, catalog, _ = _two_report_fixture()
    same_key = _proposal(((10, ("source-a",)), (20, ("source-a",))))
    result = _generate(frame, observations, catalog, same_key)
    assert result.pair_combinations_total == 1
    assert result.pairs_considered == 1
    assert result.pairs_accepted == 0
    assert result.pairs_rejected == 1
    assert result.pairs_failed == 0
    assert result.pairs_remaining == 0
    assert result.rejection_counts_by_reason["same_catalog_key"] == 1
    assert result.pairs_considered + result.pairs_remaining == result.pair_combinations_total

    coincident = (
        catalog[0],
        GeographicWedgeCatalogObject(
            "other-key", catalog[0].longitude_deg, catalog[0].latitude_deg
        ),
    )
    coincident_proposal = _proposal(((10, ("source-a",)), (20, ("other-key",))))
    coincident_result = _generate(frame, observations, coincident, coincident_proposal)
    assert coincident_result.rejection_counts_by_reason["coincident_catalog_positions"] == 1
    assert coincident_result.pairs_considered + coincident_result.pairs_remaining == (
        coincident_result.pair_combinations_total
    )


def test_near_parallel_reverse_rays_are_explicitly_rejected() -> None:
    frame, observations, catalog, _ = _two_report_fixture(
        bearings=(10.0, 10.01), ranges=(500.0, 700.0)
    )
    proposal = _proposal(((10, ("source-a",)), (20, ("source-b",))))
    result = _generate(frame, observations, catalog, proposal, min_crossing_angle_deg=0.05)

    assert result.seeds == ()
    assert result.pairs_considered == 1
    assert result.pairs_rejected == 1
    assert result.rejection_counts_by_reason["near_parallel"] == 1


def test_negative_and_out_of_range_solutions_are_rejected() -> None:
    frame = LocalFrame(_TRUTH)
    observations = (WedgeObservation(1, 0.0), WedgeObservation(2, 90.0))
    behind = (_target("a", 180.0, 300.0), _target("b", 270.0, 300.0))
    behind_proposal = _proposal(((1, ("a",)), (2, ("b",))))
    negative_result = generate_continuous_witness_seeds(
        observations,
        behind,
        (behind_proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=1_000.0,
        max_pair_evaluations=10,
        max_candidate_seeds=10,
    )
    assert negative_result.seeds == ()
    assert negative_result.rejection_counts_by_reason["negative_range"] == 1

    in_front = (_target("a", 0.0, 100.0), _target("b", 90.0, 200.0))
    range_proposal = _proposal(((1, ("a",)), (2, ("b",))))
    range_result = generate_continuous_witness_seeds(
        observations,
        in_front,
        (range_proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=50.0,
        max_pair_evaluations=10,
        max_candidate_seeds=10,
    )
    assert range_result.seeds == ()
    assert range_result.rejection_counts_by_reason["out_of_range"] == 1


def test_cell_edges_and_inclusive_exact_range_are_retained() -> None:
    frame = LocalFrame(_TRUTH)
    truth = frame.to_geographic(250.0, 125.0)
    observations = (WedgeObservation(1, 0.0), WedgeObservation(2, 90.0))
    catalog = (
        _target("a", 0.0, 1_000.0, truth),
        _target("b", 90.0, 1_000.0, truth),
    )
    proposal = _proposal(((1, ("a",)), (2, ("b",))))
    result = generate_continuous_witness_seeds(
        observations,
        catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=1_000.0,
        max_pair_evaluations=10,
        max_candidate_seeds=10,
        cell_tolerance_m=1e-6,
    )

    assert len(result.seeds) == 1
    assert result.pairs_accepted == 1
    assert result.range_tolerance_m == 1e-9
    seed = result.seeds[0]
    assert 250.0 - 1e-6 <= seed.east_m <= 250.0 + 1e-6
    assert seed.north_m == pytest.approx(125.0, abs=1e-6)
    assert seed.ranges_m == pytest.approx((1_000.0, 1_000.0), abs=1e-5)


def test_range_beyond_explicit_numerical_tolerance_is_rejected() -> None:
    frame = LocalFrame(_TRUTH)
    truth = frame.to_geographic(100.0, 100.0)
    observations = (WedgeObservation(1, 0.0), WedgeObservation(2, 90.0))
    catalog = (
        _target("a", 0.0, 1_000.0, truth),
        _target("b", 90.0, 1_000.0, truth),
    )
    proposal = _proposal(((1, ("a",)), (2, ("b",))))
    result = generate_continuous_witness_seeds(
        observations,
        catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=1_000.0 - 2e-9,
        max_pair_evaluations=10,
        max_candidate_seeds=10,
        range_tolerance_m=1e-9,
    )

    assert result.seeds == ()
    assert result.rejection_counts_by_reason["out_of_range"] == 1
    assert result.range_tolerance_m == 1e-9


def test_permutation_invariance_and_pair_budget_accounting() -> None:
    frame, observations, catalog, _ = _two_report_fixture(bearings=(45.0, 135.0))
    third_observation = WedgeObservation(30, 225.0)
    third_source = _target("source-c", 225.0, 1_700.0)
    all_observations = observations + (third_observation,)
    all_catalog = catalog + (third_source,)
    proposal = _proposal(
        (
            (10, ("source-a",)),
            (20, ("source-b",)),
            (30, ("source-c",)),
        )
    )
    full = generate_continuous_witness_seeds(
        all_observations,
        all_catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=5_000.0,
        max_pair_evaluations=20,
        max_candidate_seeds=20,
    )
    shuffled = generate_continuous_witness_seeds(
        tuple(reversed(all_observations)),
        tuple(reversed(all_catalog)),
        (replace(proposal, witness_report_keys=tuple(reversed(proposal.witness_report_keys))),),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=5_000.0,
        max_pair_evaluations=20,
        max_candidate_seeds=20,
    )
    assert full.seeds == shuffled.seeds
    assert full.pair_combinations_total == 3
    assert full.pairs_considered + full.pairs_remaining == full.pair_combinations_total
    assert full.generation_status == "completed"

    budgeted = generate_continuous_witness_seeds(
        all_observations,
        all_catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=5_000.0,
        max_pair_evaluations=1,
        max_candidate_seeds=20,
    )
    assert budgeted.generation_status == "budget_exhausted"
    assert budgeted.budget_reason == "pair_evaluations"
    assert budgeted.pairs_considered == 1
    assert budgeted.pairs_remaining == 2
    assert budgeted.pairs_considered + budgeted.pairs_remaining == budgeted.pair_combinations_total


def test_candidate_budget_is_explicit_and_does_not_hide_remaining_pairs() -> None:
    frame, observations, catalog, _ = _two_report_fixture(bearings=(45.0, 135.0))
    all_observations = observations + (WedgeObservation(30, 225.0),)
    all_catalog = catalog + (_target("source-c", 225.0, 1_700.0),)
    proposal = _proposal(
        (
            (10, ("source-a",)),
            (20, ("source-b",)),
            (30, ("source-c",)),
        )
    )
    result = generate_continuous_witness_seeds(
        all_observations,
        all_catalog,
        (proposal,),
        frame=frame,
        cell_size_m=250.0,
        max_range_m=5_000.0,
        max_pair_evaluations=20,
        max_candidate_seeds=1,
    )

    assert result.generation_status == "budget_exhausted"
    assert result.budget_reason == "candidate_seeds"
    assert result.candidate_seeds_generated == 1
    assert result.pairs_considered == 1
    assert result.pairs_remaining == 2
    assert result.pairs_considered + result.pairs_remaining == result.pair_combinations_total


def test_numerical_solver_failure_is_counted_without_losing_pair_accounting(monkeypatch) -> None:
    frame, observations, catalog, proposal = _two_report_fixture(bearings=(45.0, 135.0))

    def fail_solver(*args, **kwargs):
        raise RuntimeError("synthetic solver failure")

    monkeypatch.setattr(witness_seeds, "least_squares", fail_solver)
    result = _generate(frame, observations, catalog, proposal)

    assert result.pairs_considered == 1
    assert result.pairs_accepted == 0
    assert result.pairs_rejected == 0
    assert result.pairs_failed == 1
    assert result.pairs_remaining == 0
    assert result.rejection_counts_by_reason["numerical_failure"] == 1
    assert result.pairs_considered + result.pairs_remaining == result.pair_combinations_total


def test_duplicate_witness_report_indices_are_rejected_as_invalid_graph() -> None:
    frame, observations, catalog, proposal = _two_report_fixture()
    malformed = replace(
        proposal,
        witness_report_keys=((10, ("source-a",)), (10, ("source-b",))),
    )
    with pytest.raises(ValueError, match="repeats a report index"):
        _generate(frame, observations, catalog, malformed)
