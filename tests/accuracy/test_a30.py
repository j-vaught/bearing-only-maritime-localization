from __future__ import annotations

import importlib
import importlib.util
import time
from typing import Any, cast

import numpy as np
import pytest

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.gpu.costs import CandidateBearingInput
from bearing_localization.search.gpu.runtime import (
    iter_candidate_likelihood_costs_multi_gpu,
    run_proposal_api_on_device,
)
from bearing_localization.search.likelihood import build_likelihood_costs
from bearing_localization.search.proposals.pairwise import (
    PairwiseCatalogObject,
    PairwiseObservation,
    generate_pairwise_proposals,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("cupy") is None, reason="CuPy extra required"
)


def _candidate_rows(count: int) -> list[CandidateBearingInput]:
    return [
        CandidateBearingInput(
            f"candidate-{i:03d}",
            [float(i % 360), float((i * 17 + 90) % 360)],
            [None, "vessel"],
            [0.1, 0.2],
        )
        for i in range(count)
    ]


@pytest.mark.parametrize("device_count", [1, 2, 4])
def test_multigpu_candidate_scores_match_cpu_and_merge_stably(device_count: int) -> None:
    cp = importlib.import_module("cupy")
    available = cp.cuda.runtime.getDeviceCount()
    assert available >= device_count
    candidates = _candidate_rows(9)  # uneven tail after the per-device static chunks
    config = SearchConfig()
    rows = list(
        iter_candidate_likelihood_costs_multi_gpu(
            [0.2, 111.0], candidates, config, device_ids=tuple(range(device_count)), batch_size=1
        )
    )
    assert [row.candidate_id for row in rows] == [row.candidate_id for row in candidates]
    assert all(row.cpu_verified for row in rows)
    assert len({row.candidate_id for row in rows}) == len(candidates)
    for candidate, row in zip(candidates, rows, strict=True):
        expected = build_likelihood_costs(
            [0.2, 111.0],
            candidate.predicted_bearings_deg,
            candidate.predicted_types,
            config,
            candidate.predicted_uncertainty_deg,
        )
        np.testing.assert_array_equal(row.costs.matched_costs, expected.matched_costs)
        assert row.cpu_assignment_cost is not None
        assert row.max_cpu_matrix_error == 0.0


def test_multigpu_runtime_rejects_duplicate_or_unstable_ids_and_bounds_queue() -> None:
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        list(
            iter_candidate_likelihood_costs_multi_gpu(
                [],
                [CandidateBearingInput(1, []), CandidateBearingInput(1, [])],
                SearchConfig(),
                device_ids=(0,),
            )
        )
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        list(
            iter_candidate_likelihood_costs_multi_gpu(
                [],
                [CandidateBearingInput("b", []), CandidateBearingInput("a", [])],
                SearchConfig(),
                device_ids=(0,),
            )
        )
    with pytest.raises(ValueError, match="at least the number of devices"):
        list(
            iter_candidate_likelihood_costs_multi_gpu(
                [], [], SearchConfig(), device_ids=(0, 1), max_in_flight_chunks=1
            )
        )


def test_worker_exception_propagates_instead_of_dropping_candidates(monkeypatch) -> None:
    runtime = importlib.import_module("bearing_localization.search.gpu.runtime")
    original = runtime.iter_candidate_likelihood_costs_cupy

    def fail_one(measurements, candidates, config, **kwargs):
        if candidates[0].candidate_id == "candidate-001":
            raise ValueError("injected scoring failure")
        return original(measurements, candidates, config, **kwargs)

    monkeypatch.setattr(runtime, "iter_candidate_likelihood_costs_cupy", fail_one)
    with pytest.raises(RuntimeError, match="no complete result set"):
        list(
            iter_candidate_likelihood_costs_multi_gpu(
                [0.2],
                _candidate_rows(5),
                SearchConfig(),
                device_ids=(0, 1),
                batch_size=1,
                max_in_flight_chunks=4,
            )
        )


def test_runtime_proposal_dispatch_preserves_geographic_unsupported_contract() -> None:
    cp = importlib.import_module("cupy")
    assert cp.cuda.runtime.getDeviceCount() > 0
    with pytest.raises(NotImplementedError, match="GPU geographic proposals are unsupported"):
        run_proposal_api_on_device("geographic_pairwise", 0)


def test_multigpu_source_and_result_batches_stay_within_the_inflight_window(monkeypatch) -> None:
    runtime = importlib.import_module("bearing_localization.search.gpu.runtime")
    original = runtime.iter_candidate_likelihood_costs_cupy
    consumed = 0

    def source():
        nonlocal consumed
        for candidate in _candidate_rows(30):
            consumed += 1
            yield candidate

    def delay_initial(measurements, candidates, config, **kwargs):
        if candidates[0].candidate_id == "candidate-000":
            time.sleep(0.15)
        return original(measurements, candidates, config, **kwargs)

    monkeypatch.setattr(runtime, "iter_candidate_likelihood_costs_cupy", delay_initial)
    rows = iter_candidate_likelihood_costs_multi_gpu(
        [0.2],
        source(),
        SearchConfig(),
        device_ids=(0, 1),
        batch_size=1,
        max_in_flight_chunks=4,
    )
    first = next(rows)
    assert first.candidate_id == "candidate-000"
    assert consumed <= 5  # Four permits plus the feeder's single chunk under construction.
    assert len(list(rows)) == 29


def test_runtime_proposal_dispatch_uses_a28_pairwise_api_on_selected_device() -> None:
    from math import atan2, degrees

    origin = (1200.0, -800.0)
    targets = [(-300.0, 1700.0), (2400.0, 900.0)]
    observations = [
        PairwiseObservation(i, degrees(atan2(y - origin[1], x - origin[0])) % 360)
        for i, (x, y) in enumerate(targets)
    ]
    catalog = [PairwiseCatalogObject(f"key-{i}", x, y) for i, (x, y) in enumerate(targets)]
    expected = list(generate_pairwise_proposals(observations, catalog, max_range_m=5000.0))
    result = run_proposal_api_on_device(
        "pairwise", 0, observations, catalog, max_range_m=5000.0, batch_size=2
    )
    actual = list(result)
    assert [proposal.proposal_id for batch in actual for proposal in batch.proposals] == [
        proposal.proposal_id for batch in expected for proposal in batch.proposals
    ]


def test_early_iterator_close_drains_workers_and_releases_bounded_queues() -> None:
    consumed = 0

    def source():
        nonlocal consumed
        for candidate in _candidate_rows(12):
            consumed += 1
            yield candidate

    rows = cast(
        Any,
        iter_candidate_likelihood_costs_multi_gpu(
            [0.2], source(), SearchConfig(), device_ids=(0, 1), batch_size=1, max_in_flight_chunks=4
        ),
    )
    assert next(rows).candidate_id == _candidate_rows(1)[0].candidate_id
    rows.close()
    assert consumed == 12


@pytest.mark.parametrize("corruption", ["lost", "duplicate"])
def test_worker_rejects_lost_or_duplicate_output(monkeypatch, corruption: str) -> None:
    runtime = importlib.import_module("bearing_localization.search.gpu.runtime")
    original = runtime.iter_candidate_likelihood_costs_cupy

    def corrupt(measurements, candidates, config, **kwargs):
        values = tuple(original(measurements, candidates, config, **kwargs))
        if corruption == "lost":
            return iter(values[:-1])
        return iter(values + values[:1])

    monkeypatch.setattr(runtime, "iter_candidate_likelihood_costs_cupy", corrupt)
    with pytest.raises(RuntimeError, match="no complete result set"):
        list(
            iter_candidate_likelihood_costs_multi_gpu(
                [0.2], _candidate_rows(1), SearchConfig(), device_ids=(0,), batch_size=1
            )
        )
