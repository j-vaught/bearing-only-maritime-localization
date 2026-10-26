"""CPU and optional CuPy parity and profiling tests."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from maritime import backends


def test_cpu_kernel_uses_global_bearings_and_wraps_at_north() -> None:
    candidates = np.array([[0.0, 0.0]])
    ais = np.array([[1.0, 100.0], [-1.0, 100.0]])
    observed = np.array([359.0, 1.0])

    cost = backends.angular_cost_cpu(candidates, observed, ais)

    assert cost.shape == (1, 2, 2)
    assert cost[0, 0, 0] == pytest.approx(1.573, abs=0.01)
    assert cost[0, 1, 1] == pytest.approx(1.573, abs=0.01)
    assert cost[0, 0, 1] == pytest.approx(0.427, abs=0.01)


def test_cpu_kernel_supports_empty_dimensions() -> None:
    result = backends.angular_cost_cpu(np.empty((0, 2)), np.array([0.0]), np.empty((0, 2)))

    assert result.shape == (0, 1, 0)


@pytest.mark.parametrize(
    ("candidates", "observations", "ais", "message"),
    [
        (np.zeros((2, 3)), [0.0], np.zeros((1, 2)), "candidate_xy"),
        (np.zeros((2, 2)), [float("nan")], np.zeros((1, 2)), "finite"),
        (np.zeros((2, 2)), [0.0], np.zeros((1, 3)), "ais_xy"),
    ],
)
def test_kernel_rejects_invalid_inputs(
    candidates: np.ndarray, observations: list[float], ais: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        backends.angular_cost_cpu(candidates, np.asarray(observations), ais)


def test_benchmark_reports_cpu_and_optional_gpu_transfer_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_cupy(_: str) -> object:
        raise ModuleNotFoundError("CuPy is not installed")

    monkeypatch.setattr(backends, "import_module", missing_cupy)
    rows = backends.benchmark_backends(
        np.array([[0.0, 0.0], [1.0, 1.0]]),
        np.array([0.0, 90.0]),
        np.array([[0.0, 100.0]]),
        repeats=2,
    )

    assert rows[0].backend == "numpy"
    assert rows[0].available
    assert rows[0].kernel_s is not None and rows[0].kernel_s >= 0.0
    assert rows[0].transfer_s == 0.0
    assert rows[1].backend == "cupy"
    assert not rows[1].available
    assert "not installed" in (rows[1].error or "")
    assert rows[1].end_to_end_s is None


@pytest.mark.skipif(importlib.util.find_spec("cupy") is None, reason="CuPy is optional")
def test_cupy_kernel_agrees_with_cpu() -> None:
    candidates = np.array([[-20.0, 30.0], [100.0, -50.0]])
    observations = np.array([359.5, 25.0, 180.0])
    ais = np.array([[0.0, 500.0], [200.0, 100.0], [-300.0, 0.0]])

    expected = backends.angular_cost_cpu(candidates, observations, ais)
    actual = backends.angular_cost_cupy(candidates, observations, ais)

    np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-4)


def test_benchmark_requires_positive_repetitions() -> None:
    with pytest.raises(ValueError, match="repeats"):
        backends.benchmark_backends(np.zeros((1, 2)), np.zeros(1), np.zeros((1, 2)), repeats=0)
