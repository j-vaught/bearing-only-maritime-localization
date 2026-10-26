from __future__ import annotations

from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeCatalogObject,
    WedgeObservation,
    generate_geographic_reverse_wedge_proposals_complete,
    generate_reverse_wedge_proposals,
    generate_reverse_wedge_proposals_complete,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame


def test_reversed_inputs_preserve_stable_ids_votes_and_source_accounting():
    observations = [WedgeObservation(4, 359.9), WedgeObservation(1, 0.0)]
    catalog = [
        WedgeCatalogObject("b", 500.0, 0.0),
        WedgeCatalogObject("a", 500.0, 0.0),
    ]
    expected = generate_reverse_wedge_proposals(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_candidate_cells=64,
    )
    replay = generate_reverse_wedge_proposals(
        list(reversed(observations)),
        list(reversed(catalog)),
        max_range_m=1_000.0,
        max_candidate_cells=64,
    )
    assert [(item.candidate_id, item.vote_count) for item in replay.proposals] == [
        (item.candidate_id, item.vote_count) for item in expected.proposals
    ]
    assert replay.observations_array == expected.observations_array
    assert replay.catalog_keys == expected.catalog_keys
    assert replay.source_pairs_total == expected.source_pairs_total == 4
    assert replay.source_pairs_processed == expected.source_pairs_processed == 4
    assert replay.candidate_cells_total == expected.candidate_cells_total
    assert replay.generation_status == expected.generation_status == "completed"


def test_boundary_wrap_and_duplicate_catalog_claims_keep_one_vote_per_observation():
    observation = WedgeObservation(7, 90.0)
    one = generate_reverse_wedge_proposals(
        [observation],
        [WedgeCatalogObject("a", 1_000.0, 0.0)],
        max_range_m=1_500.0,
    )
    duplicates = generate_reverse_wedge_proposals(
        [observation],
        [
            WedgeCatalogObject("b", 1_000.0, 0.0),
            WedgeCatalogObject("a", 1_000.0, 0.0),
        ],
        max_range_m=1_500.0,
    )
    assert [(item.candidate_id, item.vote_count) for item in duplicates.proposals] == [
        (item.candidate_id, item.vote_count) for item in one.proposals
    ]
    assert all(item.vote_count == 1 for item in duplicates.proposals)
    assert all(
        item.candidate_id == f"cell:{item.cell_i}:{item.cell_j}" for item in duplicates.proposals
    )


def test_source_and_candidate_budgets_are_explicit_and_not_silent():
    observations = [WedgeObservation(0, 90.0), WedgeObservation(1, 180.0)]
    catalog = [
        WedgeCatalogObject("a", 1_000.0, 0.0),
        WedgeCatalogObject("b", 0.0, 1_000.0),
    ]
    source_limited = generate_reverse_wedge_proposals(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_source_pairs=1,
    )
    candidate_limited = generate_reverse_wedge_proposals(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_candidate_cells=3,
    )
    assert source_limited.generation_status == "budget_exhausted"
    assert source_limited.budget_reason == "source_pairs"
    assert source_limited.source_pairs_processed == 1
    assert candidate_limited.generation_status == "budget_exhausted"
    assert candidate_limited.budget_reason == "candidate_cells"
    assert candidate_limited.candidate_cells_total == 3


def test_complete_mode_matches_scalar_reference_after_all_votes():
    observations = [
        WedgeObservation(9, 359.9),
        WedgeObservation(2, 0.1),
        WedgeObservation(7, 180.0),
    ]
    catalog = [
        WedgeCatalogObject(f"key-{index}", float(index * 175 - 500), float(index * 90 - 250))
        for index in range(12)
    ]
    expected = generate_reverse_wedge_proposals(
        observations,
        catalog,
        max_range_m=2_000.0,
        cell_size_m=250.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=36,
        max_candidate_cells=100_000,
    )
    actual = generate_reverse_wedge_proposals_complete(
        observations,
        catalog,
        max_range_m=2_000.0,
        cell_size_m=250.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=36,
        max_candidate_cells=100_000,
        source_chunk_size=5,
    )
    assert [(item.candidate_id, item.vote_count) for item in actual.proposals] == [
        (item.candidate_id, item.vote_count) for item in expected.proposals
    ]
    assert actual.source_pairs_total == actual.source_pairs_processed == 36
    assert actual.candidate_cells_total == len(actual.proposals)
    assert actual.generation_status == "completed"


def test_complete_mode_keeps_source_and_storage_budgets_separate():
    observations = [WedgeObservation(0, 90.0), WedgeObservation(1, 180.0)]
    catalog = [
        WedgeCatalogObject("a", 1_000.0, 0.0),
        WedgeCatalogObject("b", 0.0, 1_000.0),
    ]
    source_limited = generate_reverse_wedge_proposals_complete(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_source_pairs=1,
        max_candidate_cells=100_000,
        source_chunk_size=2,
    )
    candidate_limited = generate_reverse_wedge_proposals_complete(
        observations,
        catalog,
        max_range_m=1_000.0,
        max_source_pairs=4,
        max_candidate_cells=3,
        source_chunk_size=2,
    )
    assert source_limited.source_pairs_processed == 1
    assert source_limited.generation_status == "budget_exhausted"
    assert source_limited.budget_reason == "source_pairs"
    assert candidate_limited.candidate_cells_total == 3
    assert candidate_limited.generation_status == "budget_exhausted"
    assert candidate_limited.budget_reason == "candidate_cells"


def test_geographic_complete_mode_retains_tile_metadata_and_votes():
    observations = [WedgeObservation(2, 90.0), WedgeObservation(1, 180.0)]
    catalog = [
        GeographicWedgeCatalogObject("b", -80.01, 32.0),
        GeographicWedgeCatalogObject("a", -80.00, 32.0),
    ]
    result = generate_geographic_reverse_wedge_proposals_complete(
        observations,
        catalog,
        frame=LocalFrame((-80.0, 32.0)),
        max_range_m=2_000.0,
        cell_size_m=250.0,
        angular_tolerance_deg=0.5,
        max_grid_bearing_error_deg=0.0,
        max_source_pairs=4,
        max_candidate_cells=100_000,
        source_chunk_size=1,
    )
    assert result.generation_status == "completed"
    assert result.source_pairs_processed == result.source_pairs_total == 4
    assert result.coordinate_model == "local_projected_tile"
    assert result.catalog_lonlat_deg is not None
    assert result.catalog_lonlat_deg.tolist() == [[-80.0, 32.0], [-80.01, 32.0]]


def test_complete_mode_exposes_neutral_histogram_and_opt_in_priority_metadata():
    result = generate_reverse_wedge_proposals_complete(
        [WedgeObservation(0, 0.0), WedgeObservation(1, 90.0)],
        [WedgeCatalogObject("near", 51.0, 51.0)],
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=2,
        max_candidate_cells=100_000,
        source_chunk_size=1,
        collect_metadata=True,
    )
    assert sum(count for _, count in result.vote_histogram) == result.candidate_cells_total
    assert result.vote_histogram == tuple(sorted(result.vote_histogram, reverse=True))
    assert all(item.ranking_key[:1] == (-item.vote_count,) for item in result.proposals)
    near_cell = next(item for item in result.proposals if item.candidate_id == "cell:0:0")
    assert near_cell.normalized_angular_residual == 1.0
    assert near_cell.evidence_score == 0.0
    assert tuple(item.ranking_key for item in result.proposals) == tuple(
        item.ranking_key for item in result.proposals
    )
