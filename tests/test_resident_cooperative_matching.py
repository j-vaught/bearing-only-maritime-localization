"""Tiny exact-assignment checks; no performance measurements in this module."""

import numpy as np
import pytest
from scipy.optimize import linear_sum_assignment

from maritime.search.gpu.resident.cooperative_matching import solve_cooperative_marker_costs

cp = pytest.importorskip("cupy")


@pytest.fixture(autouse=True)
def selected_gpu():
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except cp.cuda.runtime.CUDARuntimeError as exc:
        pytest.skip(str(exc))
    # Run this module with CUDA_VISIBLE_DEVICES=3 on the shared server.
    with cp.cuda.Device(0):
        yield


def reference(costs, ng):
    r = len(costs)
    finite = costs[:, :ng][np.isfinite(costs[:, :ng])]
    maximum = float(finite.max(initial=0.0))
    penalty = (r + 1.0) * max(1.0, maximum)
    matrix = np.full((r, ng + r), (r + 1.0) * penalty)
    matrix[:, :ng] = np.where(np.isfinite(costs[:, :ng]), costs[:, :ng], (r + 1.0) * penalty)
    for i in range(r):
        matrix[i, ng + i] = penalty
    rr, cc = linear_sum_assignment(matrix)
    pairs = [(i, j) for i, j in zip(rr, cc, strict=True) if j < ng and np.isfinite(costs[i, j])]
    return len(pairs), sum(costs[i, j] for i, j in pairs)


def assert_reference(costs, counts):
    result = solve_cooperative_marker_costs(cp.asarray(costs), cp.asarray(counts))
    cols = cp.asnumpy(result.columns)
    cardinality = cp.asnumpy(result.matched_counts)
    residuals = cp.asnumpy(result.residuals)
    assert np.array_equal(cp.asnumpy(result.errors), np.zeros(len(costs), dtype=np.int32))
    for b, ng in enumerate(counts):
        number, value = reference(costs[b], int(ng))
        assert cardinality[b] == number
        assert residuals[b] == pytest.approx(value, abs=1e-9)
        matched = [(r, int(c)) for r, c in enumerate(cols[b]) if c >= 0]
        assert len(matched) == number
        assert len({c for _, c in matched}) == number
        assert all(c < ng and np.isfinite(costs[b, r, c]) for r, c in matched)
        assert sum(costs[b, r, c] for r, c in matched) == pytest.approx(value, abs=1e-9)
    return result


def test_random_variable_counts_and_forbidden_edges_match_scipy():
    rng = np.random.default_rng(721)
    for reports, groups in ((1, 1), (3, 2), (7, 11), (12, 5), (9, 67), (89, 257)):
        costs = rng.uniform(0.0, 180.0, size=(12, reports, groups))
        costs[rng.uniform(size=costs.shape) < 0.5] = np.inf
        counts = rng.integers(0, groups + 1, size=12, dtype=np.int64)
        for b, count in enumerate(counts):
            costs[b, :, count:] = np.nan  # Padding is not input evidence.
        assert_reference(costs, counts)


def test_cardinality_dominates_residual_and_ties_repeat():
    costs = np.asarray([[[0.0, 100.0], [100.0, np.inf]], [[0.0, 0.0], [0.0, 0.0]]])
    first = assert_reference(costs, np.asarray([2, 2]))
    second = assert_reference(costs, np.asarray([2, 2]))
    np.testing.assert_array_equal(cp.asnumpy(first.columns), cp.asnumpy(second.columns))
    assert cp.asnumpy(first.matched_counts).tolist() == [2, 2]
    assert cp.asnumpy(first.residuals).tolist() == [200.0, 0.0]


def test_padded_capacity_does_not_change_assignment():
    rng = np.random.default_rng(13)
    small = rng.uniform(size=(4, 6, 9))
    small[rng.random(small.shape) < 0.3] = np.inf
    counts = np.asarray([9, 3, 0, 7])
    wide = np.full((4, 6, 2048), np.nan)
    wide[:, :, :9] = small
    a = assert_reference(small, counts)
    b = assert_reference(wide, counts)
    np.testing.assert_array_equal(cp.asnumpy(a.columns), cp.asnumpy(b.columns))
    np.testing.assert_allclose(cp.asnumpy(a.residuals), cp.asnumpy(b.residuals), atol=1e-12)


def test_no_group_and_empty_report_paths():
    result = assert_reference(np.empty((3, 5, 0)), np.zeros(3, dtype=np.int64))
    assert cp.asnumpy(result.columns).tolist() == [[-1] * 5] * 3
    assert not bool(cp.any(result.complete).item())
    empty = solve_cooperative_marker_costs(cp.empty((2, 0, 4)), cp.asarray([0, 4]))
    assert cp.asnumpy(empty.complete).tolist() == [True, True]
    assert cp.asnumpy(empty.residuals).tolist() == [0.0, 0.0]
    zero = solve_cooperative_marker_costs(cp.empty((0, 3, 4)))
    assert zero.columns.shape == (0, 3)


def test_device_error_contract_rejects_bad_data_and_counts():
    costs = np.ones((5, 2, 2))
    costs[0, 0, 0] = -1.0
    costs[1, 0, 0] = np.nan
    costs[2, :, :] = np.inf
    result = solve_cooperative_marker_costs(cp.asarray(costs), cp.asarray([1, 1, 2, -1, 3]))
    assert cp.asnumpy(result.errors).tolist() == [2, 2, 0, 1, 1]
    assert cp.asnumpy(result.matched_counts).tolist() == [0, 0, 0, 0, 0]
    overflow = solve_cooperative_marker_costs(cp.full((1, 2, 2), 1e308))
    assert cp.asnumpy(overflow.errors).tolist() == [3]


def test_solver_has_no_implicit_host_result_copy(monkeypatch):
    costs = cp.asarray([[[1.0, 2.0], [2.0, 1.0]]])

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected host assignment reconstruction")

    with monkeypatch.context() as guard:
        guard.setattr(cp, "asnumpy", forbidden)
        result = solve_cooperative_marker_costs(costs)
        assert all(
            isinstance(a, cp.ndarray)
            for a in (
                result.columns,
                result.matched_counts,
                result.residuals,
                result.errors,
                result.complete,
            )
        )
    assert cp.asnumpy(result.complete).tolist() == [True]
