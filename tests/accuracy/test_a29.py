"""A29 assignment acceleration gate and exact CPU fallback checks."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bearing_localization.search.assignment import (
    assign_likelihood_costs,
    assign_partial_cost_matrix,
)
from bearing_localization.search.gpu.assignment import assignment_acceleration_decision
from bearing_localization.search.likelihood import LikelihoodCostMatrix
from tools.profile_accuracy_assignment import run_bounded_process  # noqa: E402

from .reference_assignment import optimal_assignments


def test_prototype_gate_defers_below_twenty_percent_and_opens_at_threshold() -> None:
    assert assignment_acceleration_decision(0.199999) == "defer_cpu"
    assert assignment_acceleration_decision(0.20) == "prototype_eligible"
    assert assignment_acceleration_decision(0.8) == "prototype_eligible"


@pytest.mark.parametrize("fraction", [-0.01, 1.01, float("nan"), float("inf")])
def test_gate_rejects_invalid_assignment_fraction(fraction: float) -> None:
    with pytest.raises(ValueError):
        assignment_acceleration_decision(fraction)


def test_deferred_gate_retains_exact_cpu_solver_and_full_miss_objective() -> None:
    matched = [[1.0, 3.0], [float("inf"), float("inf")]]
    clutter = [4.0, 5.0]
    misses = [2.0, 5.0]
    decision = assignment_acceleration_decision(0.01)
    assert decision == "defer_cpu"

    actual = assign_partial_cost_matrix(matched, clutter, misses, baseline_constant=0.25)
    expected = optimal_assignments(matched, clutter, misses)
    assert actual.cost == pytest.approx(expected[0].cost + 0.25, abs=1e-8, rel=1e-10)
    assert actual.cost == pytest.approx(
        actual.baseline_constant
        + actual.matched_cost
        + actual.unmatched_report_cost
        + actual.unused_group_cost
    )
    assert actual.unused_groups == (0,)


def test_equal_cost_ties_are_repeatable_and_empty_dimensions_keep_miss_costs() -> None:
    tied = [assign_partial_cost_matrix([[0.0, 0.0]], [10.0], [0.0, 0.0]) for _ in range(4)]
    assert all(item.matches == tied[0].matches for item in tied)
    assert all(item.cost == tied[0].cost == 0.0 for item in tied)

    no_reports = assign_partial_cost_matrix(np.empty((0, 2)), [], [2.0, 3.0], baseline_constant=0.5)
    assert no_reports.matches == ()
    assert no_reports.unused_groups == (0, 1)
    assert no_reports.cost == pytest.approx(5.5)

    no_groups = assign_partial_cost_matrix(np.empty((2, 0)), [2.0, 3.0], [], baseline_constant=0.5)
    assert no_groups.matches == ()
    assert no_groups.unmatched_reports == (0, 1)
    assert no_groups.cost == pytest.approx(5.5)

    empty = assign_likelihood_costs(
        LikelihoodCostMatrix(
            np.empty((0, 0)),
            np.empty(0),
            np.empty(0),
            0.25,
            np.empty((0, 0)),
        )
    )
    assert empty.matches == ()
    assert empty.cost == pytest.approx(0.25)


def test_bounded_process_captures_successful_output() -> None:
    result = run_bounded_process(
        [sys.executable, "-c", "print('ready')"],
        timeout_s=3.0,
    )
    assert not result.timed_out
    assert result.returncode == 0
    assert result.stdout == "ready\n"


def test_bounded_process_timeout_terminates_grandchild(tmp_path) -> None:
    pid_path = tmp_path / "child.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
        "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(60)"
    )
    result = run_bounded_process(
        [sys.executable, "-c", parent_code, str(pid_path), child_code],
        timeout_s=0.4,
        terminate_grace_s=0.4,
    )
    assert result.timed_out
    assert result.elapsed_s < 5.0
    assert pid_path.is_file()
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        stat_path = Path(f"/proc/{child_pid}/stat")
        if stat_path.is_file():
            state = stat_path.read_text().rsplit(") ", maxsplit=1)[-1].split()[0]
            if state == "Z":
                break
        time.sleep(0.02)
    else:
        pytest.fail("Timed-out process group left its grandchild running.")
