"""Conservation of regions in bounded multi-device traversal."""

import pytest

from maritime.search.gpu.region_pruning import RegionCell, RegionDecision
from maritime.search.region_scheduler import make_initial_regions, traverse_regions


def test_complete_cover_and_no_top_n_cutoff() -> None:
    initial = make_initial_regions((0, 22_000, 0, 15_000))
    assert len(initial) == 6
    assert (
        sum(
            (c.bounds_en_m[1] - c.bounds_en_m[0]) * (c.bounds_en_m[3] - c.bounds_en_m[2])
            for c in initial
        )
        == 22_000 * 15_000
    )
    seen = []

    def evaluate(device, cells):
        seen.extend((device, c.bounds_en_m) for c in cells)
        return tuple(
            RegionDecision("unresolved" if c.bounds_en_m[0] == 0 else "survive", "kept")
            for c in cells
        )

    result = traverse_regions(
        initial, evaluate, device_ids=(0, 1), target_cell_size_m=1_250, batch_size=2
    )
    assert result.complete
    assert not result.unprocessed
    assert result.pruned_count == 0
    assert (
        sum(
            (r.cell.bounds_en_m[1] - r.cell.bounds_en_m[0])
            * (r.cell.bounds_en_m[3] - r.cell.bounds_en_m[2])
            for r in result.retained
        )
        == 22_000 * 15_000
    )
    assert len(result.retained) > 128
    assert any(r.decision.status == "unresolved" for r in result.retained)
    assert sum(n for _, n in result.device_evaluations) == result.evaluated_count == len(seen)


def test_budget_keeps_all_unprocessed_regions() -> None:
    initial = (RegionCell((0, 10_000, 0, 10_000)),)
    result = traverse_regions(
        initial,
        lambda device, cells: [RegionDecision("survive", "possible") for _ in cells],
        device_ids=(0, 1),
        target_cell_size_m=100,
        batch_size=2,
        max_evaluations=3,
    )
    assert not result.complete
    assert result.evaluated_count == 3
    assert result.unprocessed
    total_area = sum(
        (c.bounds_en_m[1] - c.bounds_en_m[0]) * (c.bounds_en_m[3] - c.bounds_en_m[2])
        for c in result.unprocessed
    )
    total_area += sum(
        (r.cell.bounds_en_m[1] - r.cell.bounds_en_m[0])
        * (r.cell.bounds_en_m[3] - r.cell.bounds_en_m[2])
        for r in result.retained
    )
    assert total_area == 100_000_000


def test_pruning_and_evaluator_failure_are_explicit() -> None:
    initial = make_initial_regions((0, 20_000, 0, 10_000))

    def evaluate(device, cells):
        return [
            RegionDecision("prune" if c.bounds_en_m[0] == 0 else "survive", "decision")
            for c in cells
        ]

    result = traverse_regions(initial, evaluate, device_ids=(0,), target_cell_size_m=10_000)
    assert result.pruned_count == 1
    assert len(result.retained) == 1
    with pytest.raises(ValueError, match="one valid ordered"):
        traverse_regions(
            initial, lambda device, cells: [], device_ids=(0,), target_cell_size_m=10_000
        )
