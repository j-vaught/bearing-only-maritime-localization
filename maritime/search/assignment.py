"""Exact partial one-to-one assignment for bearing likelihood costs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.optimize import linear_sum_assignment

from .likelihood import LikelihoodCostMatrix


@dataclass(frozen=True, slots=True)
class ExactAssignmentResult:
    """Optimal partial injection with its complete objective decomposition."""

    matches: tuple[tuple[int, int], ...]
    unmatched_reports: tuple[int, ...]
    unused_groups: tuple[int, ...]
    cost: float
    baseline_constant: float
    matched_cost: float
    unmatched_report_cost: float
    unused_group_cost: float


def assign_likelihood_costs(costs: LikelihoodCostMatrix) -> ExactAssignmentResult:
    """Minimize the full objective over optional injective report/group matches.

    The transformed assignment uses edge deltas `matched - clutter - miss`
    and zero-cost private report dummies. This is algebraically equivalent to
    the full objective, including every unmatched report and unused group.
    Finite negative likelihood costs and deltas are preserved; positive-infinite
    edges remain forbidden.
    """
    if not isinstance(costs, LikelihoodCostMatrix):
        raise TypeError("costs must be a LikelihoodCostMatrix.")
    matched = np.asarray(costs.matched_costs, dtype=float)
    report_miss = np.asarray(costs.report_clutter_costs, dtype=float)
    group_miss = np.asarray(costs.group_miss_costs, dtype=float)
    if matched.ndim != 2:
        raise ValueError("matched_costs must be a matrix.")
    report_count, group_count = matched.shape
    if report_miss.shape != (report_count,):
        raise ValueError("report_clutter_costs must match the report count.")
    if group_miss.shape != (group_count,):
        raise ValueError("group_miss_costs must match the group count.")
    if np.isnan(matched).any() or np.isneginf(matched).any():
        raise ValueError("matched costs must be finite or positive infinity.")
    if not np.isfinite(report_miss).all() or not np.isfinite(group_miss).all():
        raise ValueError("unmatched costs must be finite.")
    baseline = float(costs.baseline_constant)
    if not isfinite(baseline):
        raise ValueError("baseline_constant must be finite.")

    if report_count and group_count:
        delta = matched - report_miss[:, None] - group_miss[None, :]
        # Each report has one zero-cost private dummy, so all reports can remain
        # unmatched while real groups retain their one-to-one capacity.
        transformed = np.full((report_count, group_count + report_count), np.inf)
        transformed[:, :group_count] = delta
        transformed[np.arange(report_count), group_count + np.arange(report_count)] = 0.0
        rows, columns = linear_sum_assignment(transformed)
        matches = tuple(
            sorted(
                (int(row), int(column))
                for row, column in zip(rows, columns, strict=True)
                if column < group_count and np.isfinite(delta[row, column])
            )
        )
    else:
        matches = ()

    used_reports = {report for report, _ in matches}
    used_groups = {group for _, group in matches}
    unmatched = tuple(index for index in range(report_count) if index not in used_reports)
    unused = tuple(index for index in range(group_count) if index not in used_groups)
    matched_cost = sum(float(matched[i, j]) for i, j in matches)
    unmatched_cost = sum(float(report_miss[i]) for i in unmatched)
    unused_cost = sum(float(group_miss[j]) for j in unused)
    total = baseline + matched_cost + unmatched_cost + unused_cost
    return ExactAssignmentResult(
        matches=matches,
        unmatched_reports=unmatched,
        unused_groups=unused,
        cost=float(total),
        baseline_constant=baseline,
        matched_cost=float(matched_cost),
        unmatched_report_cost=float(unmatched_cost),
        unused_group_cost=float(unused_cost),
    )


def assign_partial_cost_matrix(
    matched_costs: Sequence[Sequence[float]] | np.ndarray,
    report_clutter_costs: Sequence[float] | np.ndarray,
    group_miss_costs: Sequence[float] | np.ndarray,
    *,
    baseline_constant: float = 0.0,
) -> ExactAssignmentResult:
    """Convenience wrapper for callers holding the three objective arrays."""
    matched = np.asarray(matched_costs, dtype=float)
    if matched.ndim != 2:
        raise ValueError("matched_costs must be a matrix.")
    residuals = np.zeros(matched.shape, dtype=float)
    return assign_likelihood_costs(
        LikelihoodCostMatrix(
            matched,
            np.asarray(report_clutter_costs, dtype=float),
            np.asarray(group_miss_costs, dtype=float),
            float(baseline_constant),
            residuals,
        )
    )


__all__ = ["ExactAssignmentResult", "assign_likelihood_costs", "assign_partial_cost_matrix"]
