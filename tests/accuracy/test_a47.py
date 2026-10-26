from __future__ import annotations

from bearing_localization.search.proposals.wedge import (
    WedgeCatalogObject,
    WedgeObservation,
    generate_reverse_wedge_proposals,
)


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
