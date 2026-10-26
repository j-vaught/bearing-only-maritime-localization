"""Independent CPU pool checks for whole-mode assignment expansion."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from shapely.geometry import box

from bearing_localization.search import parallel_assignment_expansion as parallel
from bearing_localization.search.assignment_expansion import ExpansionConfig, expand_assignments
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.parallel_assignment_expansion import (
    ModeTask,
    ParallelExpansionError,
    run_expansion_modes,
)
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.geospatial.vector_map import VectorMap

from . import test_assignment_expansion_review as fixture


@dataclass(frozen=True)
class _ScorerFactory:
    measurements: MeasurementSetV2
    catalog: CatalogV2
    config: SearchConfig
    source_digest: str = "source"

    def __call__(self) -> CandidateScorer:
        vector_map = VectorMap(
            None,
            crs=fixture._CRS,
            coverage_geometry=box(
                fixture._BASE_EAST - 30_000.0,
                fixture._BASE_NORTH - 30_000.0,
                fixture._BASE_EAST + 30_000.0,
                fixture._BASE_NORTH + 30_000.0,
            ),
        )
        return CandidateScorer(self.measurements, self.catalog, vector_map, self.config)


def _tasks(count: int = 4) -> tuple[list[ModeTask], _ScorerFactory, CandidateScorer]:
    scorer, seed = fixture._branch_scene()
    factory = _ScorerFactory(scorer.measurements, scorer.catalog, scorer.config)
    config = ExpansionConfig(max_branches=10)
    package_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(parallel.__file__).resolve().parents[2], text=True
    ).strip()
    tasks = [
        ModeTask(
            i,
            seed,
            config,
            2,
            package_commit=package_commit,
            input_digest=f"mode-{i}",
            source_digest="source",
        )
        for i in range(count)
    ]
    return tasks, factory, scorer


def test_four_worker_pool_matches_serial_with_distinct_mode_digests() -> None:
    tasks, factory, scorer = _tasks()
    expected = [expand_assignments(scorer, task.seed, config=task.config) for task in tasks]
    observed = {}
    phases: list[tuple[int, str, int]] = []

    def on_branch(event: dict[str, object]) -> None:
        phases.append(
            (
                cast(int, event["mode_index"]),
                cast(str, event["phase"]),
                cast(int, event["branch_id"]),
            )
        )

    result = run_expansion_modes(
        tasks,
        factory,
        worker_count=4,
        progress_callback=on_branch,
        result_callback=lambda outcome: observed.setdefault(outcome.mode_index, outcome.result),
        deadline_s=30.0,
    )

    assert result.complete
    assert result.completed_mode_ids == (0, 1, 2, 3)
    assert result.failed_mode_ids == result.pending_mode_ids == result.not_dispatched_mode_ids == ()
    assert tuple(sorted(observed)) == (0, 1, 2, 3)
    assert len(result.worker_provenance) == 4
    assert len({item.worker_id for item in result.worker_provenance}) == 4
    for task, expected_result in zip(tasks, expected, strict=True):
        actual = observed[task.mode_index]
        assert actual.status == expected_result.status
        assert actual.completed == expected_result.completed
        assert actual.budget_exhausted == expected_result.budget_exhausted
        assert actual.counters == expected_result.counters
        assert actual.best_hypothesis == expected_result.best_hypothesis
        assert actual.branch_events == expected_result.branch_events
    assert phases
    for mode_index in range(4):
        mode_phases = [phase for index, phase, _ in phases if index == mode_index]
        assert mode_phases == ["branch_started", "branch_result"] * 2


def test_deadline_returns_disjoint_incomplete_ledger() -> None:
    tasks, factory, _scorer = _tasks()

    with pytest.raises(ParallelExpansionError) as caught:
        run_expansion_modes(tasks, factory, worker_count=4, deadline_s=1e-12)

    partial = caught.value.partial_result
    all_ids = (
        set(partial.completed_mode_ids)
        | set(partial.failed_mode_ids)
        | set(partial.pending_mode_ids)
        | set(partial.not_dispatched_mode_ids)
    )
    assert all_ids == {0, 1, 2, 3}
    assert not (set(partial.completed_mode_ids) & set(partial.failed_mode_ids))
    assert not partial.complete


def test_pool_uses_spawn_and_does_not_import_runner_tools() -> None:
    assert parallel.mp.get_start_method(allow_none=True) in {None, "fork"}
    source = parallel.__file__
    assert source is not None
    text = open(source).read()
    assert 'get_context("spawn")' in text
    assert "run_assignment_expansion_diagnostic" not in text
