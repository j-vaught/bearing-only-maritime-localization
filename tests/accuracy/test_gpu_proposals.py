from __future__ import annotations

from math import atan2, degrees

import pytest

from maritime.search.gpu.proposals import (
    generate_geographic_pairwise_proposals_cupy,
    generate_pairwise_proposals_cupy,
    generate_reverse_wedge_proposals_cupy,
)
from maritime.search.proposals.pairwise import (
    PairwiseCatalogObject,
    PairwiseObservation,
    generate_pairwise_proposals,
)
from maritime.search.proposals.wedge import (
    WedgeCatalogObject,
    WedgeObservation,
    generate_reverse_wedge_proposals,
)


def _flatten(batches):
    return [p for b in batches for p in b.proposals]


def _bearing(a, b):
    return degrees(atan2(b[0] - a[0], b[1] - a[1])) % 360


def _gpu():
    cp = pytest.importorskip("cupy", reason="CuPy extra required")
    assert cp.cuda.runtime.getDeviceCount() > 0
    return cp


def test_cupy_pair_intersections_match_cpu_across_batch_sizes_and_ids():
    gpu = _gpu()
    origin = (1200.0, -800.0)
    targets = [(-300.0, 1700.0), (2400.0, 900.0), (900.0, -2200.0), (-2100.0, -500.0)]
    obs = [PairwiseObservation(i, _bearing(origin, t)) for i, t in enumerate(targets)]
    cat = [PairwiseCatalogObject(f"k{i}", *t) for i, t in enumerate(targets)]
    cpu = _flatten(generate_pairwise_proposals(obs, cat, max_range_m=5000, batch_size=2))
    for chunk in (1, 3, 17, 1000):
        actual = _flatten(
            generate_pairwise_proposals_cupy(
                obs, cat, max_range_m=5000, batch_size=3, max_pairs_per_chunk=chunk, cupy_module=gpu
            )
        )
        assert [p.proposal_id for p in actual] == [p.proposal_id for p in cpu]
        assert [p.observation_indices for p in actual] == [p.observation_indices for p in cpu]
        assert [p.catalog_keys for p in actual] == [p.catalog_keys for p in cpu]
        for a, b in zip(actual, cpu, strict=True):
            assert a.east_m == pytest.approx(b.east_m, abs=1e-10)
            assert a.north_m == pytest.approx(b.north_m, abs=1e-10)
            assert a.ranges_m == pytest.approx(b.ranges_m, abs=1e-10)


def test_pairwise_gpu_inclusive_range_negative_coordinates_and_budget_status():
    gpu = _gpu()
    obs = [PairwiseObservation(9, 270.0), PairwiseObservation(2, 0.0)]
    cat = [PairwiseCatalogObject("west", -1000.0, 0.0), PairwiseCatalogObject("north", 0.0, 1000.0)]
    full = list(generate_pairwise_proposals_cupy(obs, cat, max_range_m=1000.0, cupy_module=gpu))
    assert full[-1].generation_status == "completed"
    assert any(p.ranges_m == pytest.approx((1000.0, 1000.0)) for p in _flatten(full))


def test_sparse_wedge_gpu_matches_cpu_with_cell_boundary_wrap_and_chunks():
    gpu = _gpu()
    obs = [WedgeObservation(12, 359.9), WedgeObservation(3, 0.0), WedgeObservation(8, 180.0)]
    cat = [WedgeCatalogObject("a", -250.0, 500.0), WedgeCatalogObject("b", 0.0, 1000.0)]
    cpu = generate_reverse_wedge_proposals(obs, cat, max_range_m=1500, cell_size_m=250)
    for size in (1, 7, 10000):
        actual = generate_reverse_wedge_proposals_cupy(
            obs, cat, max_range_m=1500, cell_size_m=250, max_cells_per_chunk=size, cupy_module=gpu
        )
        assert [(p.candidate_id, p.vote_count) for p in actual.proposals] == [
            (p.candidate_id, p.vote_count) for p in cpu.proposals
        ]
        assert (
            actual.source_pairs_total,
            actual.source_pairs_processed,
            actual.candidate_cells_total,
            actual.generation_status,
        ) == (
            cpu.source_pairs_total,
            cpu.source_pairs_processed,
            cpu.candidate_cells_total,
            cpu.generation_status,
        )


def test_wedge_gpu_budget_and_duplicate_claims_keep_one_vote_per_report():
    gpu = _gpu()
    obs = [WedgeObservation(4, 90.0), WedgeObservation(1, 90.0)]
    cat = [WedgeCatalogObject("b", 500.0, 0.0), WedgeCatalogObject("a", 500.0, 0.0)]
    result = generate_reverse_wedge_proposals_cupy(
        obs, cat, max_range_m=1000, max_source_pairs=1, max_cells_per_chunk=2, cupy_module=gpu
    )
    assert result.generation_status == "budget_exhausted"
    assert result.source_pairs_processed == 1
    assert all(p.vote_count <= len(obs) for p in result.proposals)
    assert all(p.vote_count <= len(obs) for p in result.proposals)


def test_geographic_gpu_entrypoint_is_explicitly_unsupported():
    with pytest.raises(NotImplementedError, match="A19 CPU geodesic adapter"):
        generate_geographic_pairwise_proposals_cupy()


def test_wedge_gpu_candidate_cell_budget_is_explicit():
    gpu = _gpu()
    result = generate_reverse_wedge_proposals_cupy(
        [WedgeObservation(0, 180.0)],
        [WedgeCatalogObject(f"key-{i}", float(i), 500.0) for i in range(20)],
        max_range_m=1000.0,
        max_candidate_cells=4,
        max_cells_per_chunk=2,
        cupy_module=gpu,
    )
    assert result.generation_status == "budget_exhausted"
    assert result.budget_reason == "candidate_cells"
    assert result.candidate_cells_total == 4
