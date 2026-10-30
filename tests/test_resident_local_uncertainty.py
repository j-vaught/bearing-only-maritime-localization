"""Synthetic status proofs with a test-only NumPy array/kernel adapter.

Production score code is exercised unchanged. Predictions, assignments and raw
positive proofs are supplied as fixtures; this adapter is never a runtime CPU
fallback and does not run a search or evaluate real map data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from maritime.search.gpu.resident import distinct_pruning, engine, optimized


class _SyntheticResident(optimized.OptimizedResidentDevice):
    def __init__(
        self,
        *,
        angles=(0.0,),
        group_extents=None,
        feature_extents=None,
        exact=True,
        flags=0,
        point=0,
        square_scope=1,
        rejection=0,
        assumptions=True,
    ):
        # Deliberately bypass GPU input construction; only the test uses this adapter.
        self.cp = np
        self.config = SimpleNamespace(
            object_capacity=max(1, len(angles)),
            device_workspace_budget_bytes=1_000_000,
            bearing_numerical_margin_deg=0.000001,
            range_numerical_margin_m=0.001,
            angular_resolution_deg=0.1,
            radius_m=10000.0,
        )
        objects = np.zeros((max(1, len(angles)), 8))
        objects[:, 5] = feature_extents if feature_extents is not None else 0.0
        objects[:, 6] = 1
        self.world = SimpleNamespace(
            objects=objects,
            identities_complete=assumptions,
            position_support_complete=assumptions,
            chart_scope_certified=assumptions,
            max_projection_scale=1.01,
        )
        self.marker_proofs = assumptions
        self.exact_world = assumptions
        self.use_independent_chart_pruning = True
        self.use_true_bearings = True
        self.map_args = (None,) * 4
        self.object_args = (None,) * 4
        self._angles = np.asarray(angles, dtype=float)
        self._group_extents = (
            np.asarray(group_extents, dtype=float)
            if group_extents is not None
            else np.zeros(len(angles))
        )
        self._exact = np.broadcast_to(np.asarray(exact, dtype=np.int32), (len(angles),))
        self._flags, self._point = flags, point
        self._square_scope, self._rejection = square_scope, rejection
        self.rejection_calls = 0
        self.assignment = self._assignment

    def positive_marker_rejection(self, cells, bearings, tolerances):
        self.rejection_calls += 1
        return np.full(len(cells), self._rejection, dtype=np.int32)

    def _launch(self, name, n, args):
        if name == "predict_groups":
            ga, ge, gi, gx, gc, flags, point = args[20:27]
            ga.fill(0)
            ge.fill(0)
            gi.fill(-1)
            gx.fill(0)
            count = len(self._angles)
            ga[:, :count] = self._angles
            ge[:, :count] = self._group_extents
            gi[:, :count] = np.arange(count)
            gx[:, :count] = self._exact
            gc.fill(count)
            flags.fill(self._flags)
            point.fill(self._point)
        elif name in {"candidate_scope", "candidate_chart_scope"}:
            args[-1].fill(self._square_scope)
        else:
            raise AssertionError(f"Unexpected synthetic kernel {name!r}")

    @staticmethod
    def _assignment(grid, block, args):
        # A fixed one-to-one fixture assignment. It never searches for a solution.
        costs, columns = args[0], args[14]
        columns.fill(-1)
        args[15].fill(0)
        for row in range(costs.shape[1]):
            if row >= costs.shape[2]:
                continue
            valid = np.isfinite(costs[:, row, row])
            columns[:, row] = np.where(valid, row, -1)
            args[15][...] += np.where(valid, costs[:, row, row], 0)
        args[16][...] = np.sum(columns < 0, axis=1) * args[1][:, 0]
        args[17].fill(0)
        args[18][...] = args[15] + args[16]
        args[19].fill(0)


def _score(resident, bearings=(0.0,), tolerances=(0.5,)):
    return resident.score(
        np.array([[0.0, 0.0, 1.0, 1.0]]), np.asarray(bearings), np.asarray(tolerances)
    )


def test_pure_array_status_precedence_land_then_negative_then_positive():
    points = np.asarray([1, 1, 0, 0, 0, 2, 2])
    positive = np.asarray([True, False, True, True, False, False, False])
    negative = np.asarray([True, False, True, False, False, True, False])
    got = engine._terminal_status_codes(np, points, positive, negative)
    np.testing.assert_array_equal(got, [3, 3, 1, 0, 2, 1, 2])


@pytest.mark.parametrize("nominal_flag", [1, 2, 4, 8, 1 | 8, 2 | 4 | 8])
@pytest.mark.parametrize("reason", [2, 3, 4])
def test_unrelated_nominal_uncertainty_does_not_hide_certified_rejection(nominal_flag, reason):
    resident = _SyntheticResident(angles=(90.0,), flags=nominal_flag, rejection=reason)
    result = _score(resident)
    assert result["status"].tolist() == [1]
    assert int(result["flags"][0]) & nominal_flag == nominal_flag
    assert resident.rejection_calls == 1
    assert result["marker_rejection_reason"].tolist() == [reason]


def test_full_square_shoreline_coverage_gap_does_not_hide_absent_marker_support():
    resident = _SyntheticResident(angles=(), flags=1, square_scope=0, rejection=2)
    assert _score(resident)["status"].tolist() == [1]


@pytest.mark.parametrize("point", [0, 2])
def test_unknown_ray_or_observer_and_no_certified_negative_stays_unresolved(point):
    resident = _SyntheticResident(angles=(90.0,), flags=1, point=point, square_scope=0, rejection=0)
    assert _score(resident)["status"].tolist() == [2]


def test_nominal_assignment_failure_alone_does_not_reject_optimized_candidate():
    resident = _SyntheticResident(angles=(90.0,), flags=0, rejection=0)
    assert _score(resident)["status"].tolist() == [2]


def test_certified_negative_proof_wins_over_loose_nominal_positive_match():
    resident = _SyntheticResident(angles=(0.0,), exact=True, flags=0, rejection=4)
    result = _score(resident)
    assert result["matched_records"].tolist() == [[0]]
    assert result["status"].tolist() == [1]


def test_known_land_observer_wins_over_both_other_status_proofs():
    resident = _SyntheticResident(angles=(0.0,), exact=True, point=1, rejection=2)
    assert _score(resident)["status"].tolist() == [3]


def test_certified_clear_chosen_ray_can_match_with_unrelated_full_square_coverage_gap():
    resident = _SyntheticResident(angles=(0.1,), exact=True, flags=0, square_scope=0, rejection=0)
    assert _score(resident)["status"].tolist() == [0]


@pytest.mark.parametrize("flags,exact", [(1, True), (8, True), (0, False)])
def test_uncertain_chosen_ray_or_group_cannot_be_certified_positive(flags, exact):
    resident = _SyntheticResident(angles=(0.1,), exact=exact, flags=flags, rejection=0)
    assert _score(resident)["status"].tolist() == [2]


def test_unknown_observer_cannot_be_certified_positive_even_with_exact_chosen_group():
    resident = _SyntheticResident(angles=(0.1,), exact=True, flags=0, point=2, rejection=0)
    assert _score(resident)["status"].tolist() == [2]


def test_global_certificate_gap_prevents_positive_certification():
    resident = _SyntheticResident(angles=(0.1,), exact=True, assumptions=False, rejection=0)
    assert _score(resident)["status"].tolist() == [2]


def test_group_extent_can_only_establish_possible_support_not_positive_certainty():
    resident = _SyntheticResident(
        angles=(0.0,), group_extents=(20.0,), feature_extents=(0.0,), exact=True, rejection=0
    )
    assert _score(resident, bearings=(10.0,))["status"].tolist() == [2]


def test_source_feature_extent_does_not_expand_representative_bearing_certification():
    resident = _SyntheticResident(
        angles=(0.0,), group_extents=(20.0,), feature_extents=(0.2,), exact=True, rejection=0
    )
    assert _score(resident, bearings=(0.6,))["status"].tolist() == [2]
    assert _score(resident, bearings=(0.4,))["status"].tolist() == [0]


@pytest.mark.parametrize("offset", [0.5 - 0.000001, 0.5, 0.5 + 0.000001])
def test_report_boundary_and_its_numerical_margin_cannot_certify_positive(offset):
    resident = _SyntheticResident(angles=(0.0,), exact=True, rejection=0)
    assert _score(resident, bearings=(offset,))["status"].tolist() == [2]


def test_zero_reports_do_not_create_a_positive_or_negative_certificate():
    resident = _SyntheticResident(angles=(0.0,), exact=True, rejection=2)
    assert _score(resident, bearings=(), tolerances=())["status"].tolist() == [2]
    assert resident.rejection_calls == 0


@pytest.mark.parametrize("graph_flag,expected", [(0, 1), (1, 2), (2, 2), (4, 2), (1 | 4, 2)])
def test_actual_graph_wrapper_uncertainty_flags_prevent_terminal_rejection(
    monkeypatch, graph_flag, expected
):
    resident = _SyntheticResident(angles=(90.0,), flags=8)
    graph = {"base_states": np.zeros(1, dtype=np.int32)}
    solved = dict(
        feasible=np.asarray([0]),
        reasons=np.asarray([4]),
        flags=np.asarray([graph_flag]),
        proof_valid=np.asarray([graph_flag == 0]),
    )
    monkeypatch.setattr(distinct_pruning, "build_distinct_chart_graph", lambda *args, **kw: graph)
    monkeypatch.setattr(distinct_pruning, "solve_distinct_chart_graph", lambda *args, **kw: solved)
    monkeypatch.setattr(
        resident,
        "positive_marker_rejection",
        lambda cells, bearings, tolerances: (
            optimized.OptimizedResidentDevice.positive_marker_rejection(
                resident, cells, bearings, tolerances
            )
        ),
    )
    result = _score(resident)
    assert result["status"].tolist() == [expected]
    assert result["marker_rejection_reason"].tolist() == [4 if graph_flag == 0 else 0]


def test_assignment_device_error_is_raised_even_when_independent_negative_proof_exists():
    resident = _SyntheticResident(angles=(0.0,), rejection=4)
    assign = resident.assignment

    def error_assignment(grid, block, args):
        assign(grid, block, args)
        args[19].fill(1)

    resident.assignment = error_assignment
    with pytest.raises(RuntimeError, match="CUDA assignment error; no fallback"):
        _score(resident)


@pytest.mark.parametrize("scope,flags,expected", [(1, 0, 1), (0, 0, 2), (1, 1, 2)])
def test_baseline_none_proof_preserves_legacy_negative_gates(monkeypatch, scope, flags, expected):
    resident = _SyntheticResident(angles=(90.0,), square_scope=scope, flags=flags)
    monkeypatch.setattr(
        resident, "positive_marker_rejection", lambda cells, bearings, tolerances: None
    )
    assert _score(resident)["status"].tolist() == [expected]


def test_optimized_rejection_queries_degenerate_centers_and_filters_only_proof_reasons(monkeypatch):
    original = np.asarray([[0.0, 2.0, 8.0, 10.0]] * 5)
    unchanged = original.copy()
    observed = []

    def query(resident, cells, bearings, tolerances, **kwargs):
        observed.append((cells.copy(), kwargs))
        return np.asarray([0, 1, 2, 3, 4], dtype=np.int32)

    monkeypatch.setattr(optimized, "prune_chart_geometry", query)
    resident = optimized.OptimizedResidentDevice.__new__(optimized.OptimizedResidentDevice)
    setattr(resident, "cp", np)
    resident.use_true_bearings = True
    resident.use_independent_chart_pruning = True
    got = resident.positive_marker_rejection(original, np.asarray([0.0]), np.asarray([0.5]))
    np.testing.assert_array_equal(original, unchanged)
    assert len(observed) == 1
    np.testing.assert_array_equal(observed[0][0], np.asarray([[4.0, 6.0, 4.0, 6.0]] * 5))
    np.testing.assert_array_equal(got, [0, 0, 2, 3, 4])
