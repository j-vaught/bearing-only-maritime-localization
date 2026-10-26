from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys

import numpy as np
import pytest

from bearing_localization import backends
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.gpu.costs import (
    CandidateBearingInput,
    build_likelihood_costs_cupy,
    iter_candidate_likelihood_costs_cupy,
)
from bearing_localization.search.likelihood import PredictedType, build_likelihood_costs
from bearing_localization.search.telemetry import SearchTelemetry


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy extra required")
def test_gpu_a09_cost_fields_match_cpu_for_wrap_types_and_heterogeneous_sigmas() -> None:
    cp = importlib.import_module("cupy")
    observations = np.array([359.95, 22.0, 180.0])
    predicted = np.array([0.03, 20.0, 211.0, 100.0])
    observation_types = ["vessel", "physical_aid", "unknown"]
    predicted_types: list[PredictedType] = [
        "vessel",
        {"physical_aid": 0.75, "vessel": 0.25},
        None,
        {"physical_aid": 1.0},
    ]
    sigmas = np.array([0.05, 1.3, 4.0])
    predicted_sigmas = np.array([0.1, 0.4, 2.0, 0.0])
    config = SearchConfig(expected_unmatched_reports=0.7, detection_probability=0.87)

    expected = build_likelihood_costs(
        observations,
        predicted,
        predicted_types,
        config,
        predicted_sigmas,
        observation_types=observation_types,
        observation_sigmas_deg=sigmas,
    )
    telemetry = SearchTelemetry(backend="gpu")
    actual = build_likelihood_costs_cupy(
        observations,
        predicted,
        predicted_types,
        config,
        predicted_sigmas,
        observation_types=observation_types,
        observation_sigmas_deg=sigmas,
        max_pairs_per_chunk=3,
        cupy_module=cp,
        telemetry=telemetry,
    )

    np.testing.assert_allclose(actual.matched_costs, expected.matched_costs, rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(actual.residuals_deg, expected.residuals_deg, rtol=0.0, atol=0.0)
    np.testing.assert_array_equal(actual.report_clutter_costs, expected.report_clutter_costs)
    np.testing.assert_array_equal(actual.group_miss_costs, expected.group_miss_costs)
    assert actual.baseline_constant == expected.baseline_constant
    report = telemetry.as_dict()
    assert {stage["name"] for stage in report["stages"]} == {
        "cost_transfer_in",
        "cost_kernel",
        "cost_transfer_out",
    }
    assert sum(item["bytes"] for item in report["transfers"]) > 0
    assert actual.total_for_assignment([(0, 0), (1, 1)]) == pytest.approx(
        expected.total_for_assignment([(0, 0), (1, 1)]), abs=1e-10
    )
    pool = cp.get_default_memory_pool()
    after_first = pool.used_bytes()
    for _ in range(4):
        build_likelihood_costs_cupy(
            observations,
            predicted,
            predicted_types,
            config,
            predicted_sigmas,
            observation_types=observation_types,
            observation_sigmas_deg=sigmas,
            max_pairs_per_chunk=3,
            cupy_module=cp,
        )
    assert pool.used_bytes() <= after_first


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy extra required")
def test_gpu_costs_support_empty_dimensions_and_legacy_adapter() -> None:
    cp = importlib.import_module("cupy")
    cfg = SearchConfig()
    empty_reports = build_likelihood_costs_cupy([], [10.0], [None], cfg, cupy_module=cp)
    assert empty_reports.matched_costs.shape == (0, 1)
    empty_groups = build_likelihood_costs_cupy([10.0], [], [], cfg, cupy_module=cp)
    assert empty_groups.matched_costs.shape == (1, 0)
    legacy = SearchConfig(objective_version="legacy_corrected_v0")
    costs = build_likelihood_costs_cupy(
        [0.0, 0.0], [1.0], [None], legacy, observation_sigmas_deg=[0.5, 2.0], cupy_module=cp
    )
    np.testing.assert_allclose(costs.matched_costs[:, 0], [2.0, 0.5])


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy extra required")
def test_lazy_candidate_batches_rerank_close_cases_with_cpu_authority() -> None:
    cp = importlib.import_module("cupy")
    candidates = (
        CandidateBearingInput("first", [0.01, 120.0], [None, "vessel"], [0.1, 0.2]),
        CandidateBearingInput("second", [359.99, 80.0], [None, "vessel"], [0.1, 0.2]),
        CandidateBearingInput("third", [20.0], [None], [0.0]),
    )
    rows = list(
        iter_candidate_likelihood_costs_cupy(
            [0.0, 100.0],
            candidates,
            SearchConfig(),
            batch_size=2,
            max_pairs_per_chunk=1,
            cupy_module=cp,
        )
    )
    assert [row.candidate_id for row in rows] == ["first", "second", "third"]
    assert rows[0].cpu_assignment_cost is not None and rows[0].cpu_verified
    assert rows[1].cpu_assignment_cost is not None and rows[1].cpu_verified
    assert rows[0].max_cpu_matrix_error is not None
    assert rows[0].max_cpu_matrix_error <= 1e-10
    # The last batch has one member and is necessarily reranked as its local winner.
    assert rows[-1].cpu_assignment_cost is not None and rows[-1].cpu_verified


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy extra required")
@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy extra required")
def test_cpu_verify_all_covers_fixed_candidate_set() -> None:
    cp = importlib.import_module("cupy")
    candidates = (
        CandidateBearingInput("a", [0.0, 80.0], [None, "vessel"]),
        CandidateBearingInput("b", [1.0, 90.0], [None, "vessel"]),
        CandidateBearingInput("c", [2.0, 100.0], [None, "vessel"]),
    )
    rows = list(
        iter_candidate_likelihood_costs_cupy(
            [0.1, 95.0],
            candidates,
            SearchConfig(),
            batch_size=2,
            cpu_verify_all=True,
            cupy_module=cp,
        )
    )
    assert len(rows) == len(candidates)
    assert all(row.cpu_verified and row.cpu_assignment_cost is not None for row in rows)
    assert all(row.max_cpu_matrix_error is not None for row in rows)
    assert all(
        row.max_cpu_matrix_error <= 1e-10 for row in rows if row.max_cpu_matrix_error is not None
    )


def test_cupy_angular_cost_rebases_large_global_coordinates_and_respects_sigma_tolerance() -> None:
    cp = importlib.import_module("cupy")
    origin = 1.0e9
    candidates = np.array([[origin, -origin], [origin + 10.0, -origin + 15.0]])
    ais = np.array([[origin + 0.25, -origin + 100.0], [origin - 3.0, -origin + 17.0]])
    observations = np.array([359.7, 0.2, 90.0])
    expected = backends.angular_cost_cpu(candidates, observations, ais)
    actual = backends.angular_cost_cupy(candidates, observations, ais, cupy_module=cp)
    sigmas = np.array([0.5, 0.05, 2.0])
    tolerance = np.minimum(0.001, 0.01 * sigmas)
    errors = np.abs(actual - expected)
    assert float(errors.max()) <= float(tolerance.min())


def test_cost_chunk_size_validation_and_cpu_only_import_compatibility() -> None:
    assert list(iter_candidate_likelihood_costs_cupy([], [], SearchConfig())) == []
    with pytest.raises(ValueError, match="max_pairs_per_chunk"):
        build_likelihood_costs_cupy([0.0], [0.0], [None], SearchConfig(), max_pairs_per_chunk=0)
    # A fresh CPU process can import the GPU API module without CuPy.
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;\n"
                "import bearing_localization.search.gpu.costs;\n"
                'assert "cupy" not in sys.modules'
            ),
        ],
        check=True,
    )
