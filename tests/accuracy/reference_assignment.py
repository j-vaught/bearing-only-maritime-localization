"""Small exhaustive assignment oracle, intentionally independent of production solvers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import inf, isfinite


@dataclass(frozen=True, slots=True)
class Assignment:
    """One feasible partial injection and its complete objective value."""

    matches: tuple[tuple[int, int], ...]
    unmatched_reports: tuple[int, ...]
    unused_groups: tuple[int, ...]
    cost: float


def enumerate_partial_assignments(
    real_costs: Sequence[Sequence[float]],
    report_miss_costs: Sequence[float],
    group_miss_costs: Sequence[float],
) -> tuple[Assignment, ...]:
    """Enumerate every partial one-to-one assignment in deterministic order.

    ``real_costs[i][j]`` is the cost of matching report ``i`` to predicted
    group ``j``. Positive infinity forbids an edge. Every unmatched report and
    every unused predicted group contributes its corresponding miss cost.
    Negative finite edge costs are valid. The returned tuple includes every
    feasible assignment, including all objective ties.
    """
    n_reports = len(report_miss_costs)
    n_groups = len(group_miss_costs)
    if len(real_costs) != n_reports:
        raise ValueError("real_costs row count must equal the report miss-cost count")
    if any(len(row) != n_groups for row in real_costs):
        raise ValueError("every real_costs row must match the group miss-cost count")
    if any(not isfinite(float(cost)) for cost in (*report_miss_costs, *group_miss_costs)):
        raise ValueError("miss costs must be finite")
    for row in real_costs:
        for raw_cost in row:
            cost = float(raw_cost)
            if cost != inf and not isfinite(cost):
                raise ValueError("real costs must be finite or positive infinity")

    assignments: list[Assignment] = []
    matches: list[tuple[int, int]] = []
    unmatched: list[int] = []
    used_groups: set[int] = set()

    def visit(report_index: int, accumulated_cost: float) -> None:
        if report_index == n_reports:
            unused = tuple(index for index in range(n_groups) if index not in used_groups)
            assignments.append(
                Assignment(
                    matches=tuple(matches),
                    unmatched_reports=tuple(unmatched),
                    unused_groups=unused,
                    cost=accumulated_cost + sum(float(group_miss_costs[index]) for index in unused),
                )
            )
            return

        unmatched.append(report_index)
        visit(report_index + 1, accumulated_cost + float(report_miss_costs[report_index]))
        unmatched.pop()

        for group_index, raw_cost in enumerate(real_costs[report_index]):
            edge_cost = float(raw_cost)
            if group_index in used_groups or edge_cost == inf:
                continue
            used_groups.add(group_index)
            matches.append((report_index, group_index))
            visit(report_index + 1, accumulated_cost + edge_cost)
            matches.pop()
            used_groups.remove(group_index)

    visit(0, 0.0)
    return tuple(assignments)


def optimal_assignments(
    real_costs: Sequence[Sequence[float]],
    report_miss_costs: Sequence[float],
    group_miss_costs: Sequence[float],
) -> tuple[Assignment, ...]:
    """Return all assignments attaining the minimum full objective."""
    assignments = enumerate_partial_assignments(real_costs, report_miss_costs, group_miss_costs)
    best_cost = min((assignment.cost for assignment in assignments), default=0.0)
    return tuple(assignment for assignment in assignments if assignment.cost == best_cost)
