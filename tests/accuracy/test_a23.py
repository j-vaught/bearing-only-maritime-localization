from __future__ import annotations

from dataclasses import replace
from math import atan2, degrees

from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeCatalogObject,
    WedgeObservation,
    generate_geographic_reverse_wedge_proposals,
    generate_reverse_wedge_proposals,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame


def _bearing(observer: tuple[float, float], target: tuple[float, float]) -> float:
    return degrees(atan2(target[0] - observer[0], target[1] - observer[1])) % 360.0


def test_duplicate_catalog_claims_count_once_per_observation_and_keep_inputs():
    observation = WedgeObservation(7, 90.0)
    one = generate_reverse_wedge_proposals(
        [observation], [WedgeCatalogObject("a", 1_000.0, 0.0)], max_range_m=1_500.0
    )
    duplicates = generate_reverse_wedge_proposals(
        [observation],
        [WedgeCatalogObject("b", 1_000.0, 0.0), WedgeCatalogObject("a", 1_000.0, 0.0)],
        max_range_m=1_500.0,
    )
    assert {p.candidate_id: p.vote_count for p in one.proposals} == {
        p.candidate_id: p.vote_count for p in duplicates.proposals
    }
    assert all(p.vote_count == 1 for p in duplicates.proposals)
    assert duplicates.observations_array == ((7, 90.0),)
    assert duplicates.report_indices_array.tolist() == [7]
    assert duplicates.bearings_deg_array.tolist() == [90.0]
    assert duplicates.catalog_metadata["keys"] == ("a", "b")
    assert duplicates.catalog_xy_m.shape == (2, 2)
    assert duplicates.catalog_keys_array.tolist() == ["a", "b"]
    assert duplicates.proposal_cell_indices_array.shape == (len(duplicates.proposals), 2)
    assert duplicates.proposal_positions_en_m.shape == (len(duplicates.proposals), 2)
    assert duplicates.vote_counts_array.tolist() == [
        proposal.vote_count for proposal in duplicates.proposals
    ]


def test_wraparound_parallel_bearings_and_half_open_grid_boundaries_are_stable():
    observations = [
        WedgeObservation(11, 359.9),
        WedgeObservation(2, 0.1),
        WedgeObservation(8, 0.0),
    ]
    catalog = [WedgeCatalogObject("north", 100.0, 1_000.0)]
    first = generate_reverse_wedge_proposals(
        observations, catalog, max_range_m=1_500.0, angular_tolerance_deg=0.5
    )
    replay = generate_reverse_wedge_proposals(
        list(reversed(observations)), list(reversed(catalog)), max_range_m=1_500.0
    )
    assert [(p.candidate_id, p.vote_count) for p in first.proposals] == [
        (p.candidate_id, p.vote_count) for p in replay.proposals
    ]
    assert first.report_indices_array.tolist() == [2, 8, 11]
    assert all(p.candidate_id == f"cell:{p.cell_i}:{p.cell_j}" for p in first.proposals)
    assert any(abs(p.east_m - 125.0) <= 125.0 for p in first.proposals)
    assert (
        max(proposal.north_m for proposal in first.proposals)
        - min(proposal.north_m for proposal in first.proposals)
        >= 1_000.0
    )


def test_votes_do_not_overflow_fixed_width_accumulators():
    observations = [WedgeObservation(index, 90.0) for index in range(300)]
    result = generate_reverse_wedge_proposals(
        observations, [WedgeCatalogObject("east", 1_000.0, 0.0)], max_range_m=1_500.0
    )
    assert max(proposal.vote_count for proposal in result.proposals) == 300
    assert result.generation_status == "completed"


def test_source_and_candidate_budgets_report_incomplete_generation():
    observations = [WedgeObservation(0, 90.0), WedgeObservation(1, 180.0)]
    catalog = [WedgeCatalogObject("a", 1_000.0, 0.0), WedgeCatalogObject("b", 0.0, 1_000.0)]
    source_limited = generate_reverse_wedge_proposals(
        observations, catalog, max_range_m=1_000.0, max_source_pairs=1
    )
    assert source_limited.generation_status == "budget_exhausted"
    assert source_limited.budget_reason == "source_pairs"
    assert source_limited.source_pairs_processed == 1
    candidate_limited = generate_reverse_wedge_proposals(
        observations, catalog, max_range_m=1_000.0, max_candidate_cells=3
    )
    assert candidate_limited.generation_status == "budget_exhausted"
    assert candidate_limited.budget_reason == "candidate_cells"
    assert candidate_limited.candidate_cells_total == 3


def test_dense_catalog_is_bounded_and_permutation_deterministic():
    observations = [WedgeObservation(0, 180.0)]
    catalog = [WedgeCatalogObject(f"key-{index:04d}", float(index), 500.0) for index in range(500)]
    result = generate_reverse_wedge_proposals(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_candidate_cells=16,
    )
    permuted = generate_reverse_wedge_proposals(
        observations,
        list(reversed(catalog)),
        max_range_m=1_000.0,
        max_candidate_cells=16,
    )
    assert result.generation_status == "budget_exhausted"
    assert result.budget_reason == "candidate_cells"
    assert result.candidate_cells_total == 16
    assert [p.candidate_id for p in result.proposals] == [
        p.candidate_id for p in permuted.proposals
    ]
    assert [p.vote_count for p in result.proposals] == [p.vote_count for p in permuted.proposals]


def test_a18_tiny_scene_vote_recall_is_invariant_to_label_availability():
    truth = (1_000.0, 1_000.0)
    targets = [(1_000.0, 2_000.0), (2_000.0, 1_000.0), (500.0, 500.0)]
    observations = [
        WedgeObservation(index, _bearing(truth, target)) for index, target in enumerate(targets)
    ]
    catalog = [
        WedgeCatalogObject(f"target-{index}", *target) for index, target in enumerate(targets)
    ]
    labels_enabled = generate_reverse_wedge_proposals(
        observations,
        [replace(item, normalized_type="vessel") for item in catalog],
        max_range_m=1_500.0,
    )
    # Type labels remain metadata and cannot alter geometric vote coverage.
    labels_disabled = generate_reverse_wedge_proposals(
        observations,
        [replace(item, normalized_type=None) for item in catalog],
        max_range_m=1_500.0,
    )
    assert [(p.candidate_id, p.vote_count) for p in labels_enabled.proposals] == [
        (p.candidate_id, p.vote_count) for p in labels_disabled.proposals
    ]
    assert labels_enabled.catalog_types == ("vessel", "vessel", "vessel")
    assert labels_disabled.catalog_types == (None, None, None)
    assert any(
        abs(proposal.east_m - truth[0]) <= 250.0
        and abs(proposal.north_m - truth[1]) <= 250.0
        and proposal.vote_count >= 3
        for proposal in labels_enabled.proposals
    )


def test_catalog_on_grid_boundaries_includes_both_neighboring_cell_columns():
    result = generate_reverse_wedge_proposals(
        [WedgeObservation(0, 0.0)],
        [WedgeCatalogObject("north", 0.0, 1_000.0)],
        max_range_m=1_000.0,
    )
    ids = {proposal.candidate_id for proposal in result.proposals}
    assert "cell:-1:-1" in ids
    assert "cell:0:-1" in ids


def test_geographic_adapter_uses_local_tile_and_retains_geodetic_metadata():
    frame = LocalFrame((-179.999, 0.0))
    result = generate_geographic_reverse_wedge_proposals(
        [WedgeObservation(4, 270.0)],
        [GeographicWedgeCatalogObject("dateline", 179.999, 0.0)],
        frame=frame,
        max_range_m=1_000.0,
        max_grid_bearing_error_deg=0.0,
    )
    metadata = result.catalog_metadata
    assert result.coordinate_model == "local_projected_tile"
    assert metadata["frame_origin_lonlat"] == (-179.999, 0.0)
    assert isinstance(metadata["frame_crs"], str)
    assert result.catalog_lonlat_deg is not None
    assert result.catalog_lonlat_deg.tolist() == [[179.999, 0.0]]
    assert result.catalog_xy_m[0, 0] < 0.0
    assert result.bearings_deg_array.tolist() == [270.0]


def test_tiny_cells_report_bounded_raster_budget_exhaustion():
    result = generate_reverse_wedge_proposals(
        [WedgeObservation(0, 90.0)],
        [WedgeCatalogObject("east", 1_000.0, 0.0)],
        max_range_m=1_000_000.0,
        cell_size_m=1e-9,
        max_candidate_cells=8,
    )
    assert result.generation_status == "budget_exhausted"
    assert result.budget_reason == "candidate_cells"
