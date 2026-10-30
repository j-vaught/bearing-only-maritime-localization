"""Hard-gated marker matching checks against SciPy's exact assignment."""

import numpy as np
import pytest
from scipy.optimize import linear_sum_assignment

from maritime.search.gpu.deterministic_marker_matching import match_positive_markers_cupy

cp = pytest.importorskip("cupy")
try:
    if cp.cuda.runtime.getDeviceCount() == 0:
        pytest.skip("CUDA device unavailable", allow_module_level=True)
except Exception as exc:
    pytest.skip(f"CUDA device unavailable: {exc}", allow_module_level=True)


def _reference(pred, groups, tol, valid_detections=None, valid_groups=None):
    pred = np.asarray(pred, dtype=np.float64)
    groups = np.asarray(groups, dtype=np.float64)
    tol = np.broadcast_to(np.asarray(tol, dtype=np.float64), pred.shape)
    rd = np.abs(pred[:, None] - groups[None, :])
    rd = np.minimum(rd, 360.0 - rd)
    allowed = rd <= tol[:, None]
    if valid_detections is not None:
        allowed &= np.asarray(valid_detections, dtype=bool)[:, None]
    if valid_groups is not None:
        allowed &= np.asarray(valid_groups, dtype=bool)[None, :]
    # Dummies have a cost larger than every possible residual sum, so scipy
    # first maximizes cardinality and then minimizes total residual.
    penalty = 180.0 * len(pred) + 1.0
    matrix = np.full((len(pred), len(groups) + len(pred)), penalty)
    matrix[:, : len(groups)] = np.where(allowed, rd, 1e100)
    matrix[:, len(groups) :] = penalty
    rr, cc = linear_sum_assignment(matrix)
    pairs = tuple((int(r), int(c)) for r, c in zip(rr, cc) if c < len(groups) and allowed[r, c])
    return pairs, sum(rd[r, c] for r, c in pairs)


def test_hard_gate_wrap_duplicate_reports_and_cardinality_before_residual():
    pred = np.array([[359.8, 0.2, 180.0], [10.0, 10.0, 10.0]])
    groups = np.array([[0.1, 359.9, 180.0], [10.0, 10.0, 20.0]])
    tol = np.array([[0.4, 0.4, 0.1], [0.0, 0.0, 0.0]])
    result = match_positive_markers_cupy(pred, groups, tol, cupy_module=cp)
    for b in range(2):
        pairs, cost = _reference(pred[b], groups[b], tol[b])
        assert len(result.matches[b]) == len(pairs)
        assert len({g for _, g in result.matches[b]}) == len(result.matches[b])
        assert len({r for r, _ in result.matches[b]}) == len(result.matches[b])
        assert float(cp.asnumpy(result.residuals_deg)[b]) == pytest.approx(cost)
        assert result.complete[b] is (len(pairs) == len(pred[b]))
        assert result.status[b] == ("complete" if result.complete[b] else "infeasible")
    assert result.complete[0]
    assert not result.complete[1]


def test_padded_rows_variable_group_masks_and_empty_aid_observations():
    pred = np.array([[5.0, np.nan, 100.0], [1.0, 2.0, 3.0]])
    groups = np.array([[5.1, np.nan, 200.0], [np.nan, np.nan, np.nan]])
    vd = np.array([[True, False, False], [True, True, True]])
    vg = np.array([[True, False, False], [False, False, False]])
    result = match_positive_markers_cupy(
        pred, groups, 1.0, valid_detections=vd, valid_groups=vg, cupy_module=cp
    )
    assert result.matches[0] == ((0, 0),)
    assert result.status == ("complete", "infeasible")
    assert result.complete == (True, False)
    assert float(cp.asnumpy(result.residuals_deg)[0]) == pytest.approx(0.1)
    assert float(cp.asnumpy(result.residuals_deg)[1]) == 0.0


def test_all_compatible_is_injective_and_uncertain_possible_completion_is_unresolved():
    pred = np.array([[0.0, 0.0, 0.0]])
    groups = np.array([[0.0, 0.0, 0.0]])
    all_edges = np.ones((1, 3, 3), dtype=bool)
    full = match_positive_markers_cupy(pred, groups, 0.0, valid_edges=all_edges, cupy_module=cp)
    assert full.complete == (True,)
    assert len(full.matches[0]) == 3
    assert len({g for _, g in full.matches[0]}) == 3

    uncertain = np.zeros((1, 2, 2), dtype=bool)
    uncertain[0, 1, 1] = True
    partial = match_positive_markers_cupy(
        pred[:, :2],
        groups[:, :2],
        0.0,
        valid_edges=np.array([[[True, False], [False, False]]]),
        unresolved_edges=uncertain,
        cupy_module=cp,
    )
    assert partial.status == ("unresolved",)
    assert partial.complete == (False,)
    assert partial.possible_complete == (True,)
    assert len(partial.matches[0]) == 1


def test_empty_detection_batches_are_complete_with_zero_residual():
    result = match_positive_markers_cupy(np.empty((2, 0)), np.zeros((2, 3)), 5.0, cupy_module=cp)
    assert result.matches == ((), ())
    assert result.status == ("complete", "complete")
    assert result.complete == (True, True)
    assert np.array_equal(cp.asnumpy(result.residuals_deg), np.zeros(2))


def test_unresolved_edge_can_complete_despite_nominal_bearing_mismatch():
    result = match_positive_markers_cupy(
        np.array([[90.0]]),
        np.array([[0.0]]),
        1.0,
        unresolved_edges=np.array([[[True]]]),
        cupy_module=cp,
    )
    assert result.matches == ((),)
    assert result.complete == (False,)
    assert result.possible_complete == (True,)
    assert result.status == ("unresolved",)
