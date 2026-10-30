"""GPU partial-assignment equivalence checks against the exact CPU solver."""

from __future__ import annotations

import numpy as np
import pytest

from maritime.search.assignment import assign_partial_cost_matrix
from maritime.search.gpu.batch_assignment import (
    assign_partial_cost_matrices_cupy,
    assign_partial_cost_matrices_rawkernel,
)

cp = pytest.importorskip("cupy")
try:
    _GPU_COUNT = cp.cuda.runtime.getDeviceCount()
except Exception as exc:  # CuPy may be installed without a usable CUDA device.
    pytest.skip(f"CUDA device unavailable: {exc}", allow_module_level=True)
if _GPU_COUNT == 0:
    pytest.skip("CUDA device unavailable", allow_module_level=True)


def _check_batch(
    matched,
    clutter,
    miss,
    baselines=0.0,
    group_counts=None,
    solver=assign_partial_cost_matrices_cupy,
):
    result = solver(
        matched,
        clutter,
        miss,
        baseline_constants=baselines,
        group_counts=group_counts,
        cupy_module=cp,
    )
    device_costs = cp.asnumpy(result.device_costs)
    assert device_costs.shape == (matched.shape[0],)
    for b in range(matched.shape[0]):
        n_groups = matched.shape[2] if group_counts is None else int(group_counts[b])
        cpu = assign_partial_cost_matrix(
            matched[b, :, :n_groups],
            clutter[b],
            miss[b, :n_groups],
            baseline_constant=float(np.broadcast_to(baselines, (matched.shape[0],))[b]),
        )
        assert device_costs[b] == pytest.approx(cpu.cost, rel=1e-10, abs=1e-10)
        # Multiple optimal assignments are valid when the objective ties.
        assert _objective(
            matched[b, :, :n_groups],
            clutter[b],
            miss[b, :n_groups],
            result.matches[b],
            baselines,
            b,
        ) == pytest.approx(cpu.cost, rel=1e-10, abs=1e-10)
        assert result.unmatched_reports[b] == cpu.unmatched_reports
        assert result.unused_groups[b] == tuple(
            j for j in range(n_groups) if j not in {g for _, g in result.matches[b]}
        )
    return result


def _objective(matrix, clutter, miss, matches, baselines, b):
    baseline = (
        float(np.asarray(baselines).reshape(-1)[b]) if np.ndim(baselines) else float(baselines)
    )
    used_r = {i for i, _ in matches}
    used_g = {j for _, j in matches}
    return (
        baseline
        + sum(float(matrix[i, j]) for i, j in matches)
        + sum(float(clutter[i]) for i in range(len(clutter)) if i not in used_r)
        + sum(float(miss[j]) for j in range(len(miss)) if j not in used_g)
    )


def test_gpu_batch_matches_cpu_for_unmatched_forbidden_edges_ties_and_type_filtered_edges():
    inf = np.inf
    matched = np.asarray(
        [
            [[1.0, inf, 9.0], [inf, 2.0, 8.0], [7.0, 6.0, inf]],
            [[0.0, 0.0, inf], [0.0, 0.0, inf], [inf, inf, inf]],
            [[inf, inf, inf], [inf, inf, inf], [inf, inf, inf]],
        ]
    )
    clutter = np.asarray([[3.0, 3.0, 3.0], [1.0, 1.0, 1.0], [0.25, 0.5, 0.75]])
    miss = np.asarray([[4.0, 4.0, 4.0], [1.0, 1.0, 1.0], [1.0, 2.0, 3.0]])
    # +inf edges model type-incompatible report/group pairs already excluded by scoring.
    result = _check_batch(matched, clutter, miss, baselines=np.asarray([0.5, -2.0, 1.25]))
    _check_batch(
        matched,
        clutter,
        miss,
        baselines=np.asarray([0.5, -2.0, 1.25]),
        solver=assign_partial_cost_matrices_rawkernel,
    )
    assert result.unmatched_reports[2] == (0, 1, 2)
    assert result.unused_groups[2] == (0, 1, 2)


def test_gpu_batch_handles_empty_and_rectangular_matrices_and_per_candidate_group_sizes():
    matched = np.asarray(
        [
            [[2.0, 100.0, 100.0], [100.0, 3.0, 100.0]],
            [[5.0, 100.0, 100.0], [100.0, 100.0, 100.0]],
        ]
    )
    clutter = np.asarray([[8.0, 8.0], [1.0, 1.0]])
    miss = np.asarray([[9.0, 9.0, 999.0], [2.0, 999.0, 999.0]])
    _check_batch(matched, clutter, miss, group_counts=np.asarray([2, 1]))
    _check_batch(
        matched,
        clutter,
        miss,
        group_counts=np.asarray([2, 1]),
        solver=assign_partial_cost_matrices_rawkernel,
    )
    for shape in ((2, 0), (0, 3), (0, 0), (1, 4)):
        r, g = shape
        _check_batch(np.zeros((1, r, g)), np.ones((1, r)), np.ones((1, g)), baselines=0.125)


def test_rawkernel_realistic_random_batches_with_mixed_group_counts():
    rng = np.random.default_rng(261026)
    batch, reports, groups = 16, 24, 28
    matched = rng.normal(loc=0.0, scale=4.0, size=(batch, reports, groups))
    # Forbidden edges represent impossible/type-incompatible report and group pairs.
    matched[rng.random(size=matched.shape) < 0.30] = np.inf
    clutter = rng.uniform(-2.0, 3.0, size=(batch, reports))
    misses = rng.uniform(-2.0, 3.0, size=(batch, groups))
    group_counts = rng.integers(0, groups + 1, size=batch, dtype=np.int64)
    group_counts[:4] = (0, 1, groups - 1, groups)
    baselines = rng.normal(size=batch)
    _check_batch(
        matched,
        clutter,
        misses,
        baselines=baselines,
        group_counts=group_counts,
        solver=assign_partial_cost_matrices_rawkernel,
    )
