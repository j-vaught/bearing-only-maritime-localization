"""Deterministic ordering for bounded version-two candidate streams.

Candidate generation and candidate evaluation are separate concerns.  This
module only reorders an existing finite stream.  Every input position is
returned exactly once, including positions whose map status is unknown or
invalid.  The order puts known-water positions first and visits occupied
spatial bins in a bit-reversed traversal so a short prefix covers the domain.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from math import ceil, isfinite, log2
from typing import Literal

CandidatePosition = tuple[float, float]
WaterStatus = Literal["water", "unknown", "land"]
WaterStatusQuery = Callable[[CandidatePosition], str]


def _status_rank(status: str) -> int:
    """Return the ordering rank for a map status."""

    if status == "water":
        return 0
    if status == "unknown":
        return 1
    return 2


def _reverse_bits(value: int, width: int) -> int:
    reversed_value = 0
    for _ in range(width):
        reversed_value = (reversed_value << 1) | (value & 1)
        value >>= 1
    return reversed_value


def _spread_bin_order(bins_per_axis: int) -> tuple[tuple[int, int], ...]:
    """Return a deterministic broad-first order of square spatial bins."""

    padded = 1
    while padded < bins_per_axis:
        padded <<= 1
    width = int(log2(padded))

    def morton(value_x: int, value_y: int) -> int:
        code = 0
        for bit in range(width):
            code |= ((value_x >> bit) & 1) << (2 * bit)
            code |= ((value_y >> bit) & 1) << (2 * bit + 1)
        return code

    order: list[tuple[int, int, int]] = []
    for bin_y in range(bins_per_axis):
        for bin_x in range(bins_per_axis):
            order.append(
                (
                    morton(_reverse_bits(bin_x, width), _reverse_bits(bin_y, width)),
                    bin_x,
                    bin_y,
                )
            )
    order.sort()
    return tuple((bin_x, bin_y) for _, bin_x, bin_y in order)


def order_candidate_positions(
    positions: Iterable[CandidatePosition],
    *,
    water_status: WaterStatusQuery | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    bins_per_axis: int | None = None,
) -> tuple[CandidatePosition, ...]:
    """Return all positions in deterministic status-prioritized spatial order.

    The input is materialized because broad coverage requires seeing all
    occupied bins before selecting their round-robin prefix.  No position is
    filtered or deduplicated.  bounds should be the declared search domain;
    when omitted, the finite input extent is used.  A supplied map query is
    called once per input position and may return water, unknown, land, or
    another value, with all non-water values retained after the known-water
    group.
    """

    materialized: list[CandidatePosition] = []
    for raw_position in positions:
        try:
            east_m, north_m = raw_position
        except (TypeError, ValueError) as exc:
            raise ValueError("candidate positions must contain two coordinates.") from exc
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("candidate positions must contain finite coordinates.")
        materialized.append((float(east_m), float(north_m)))
    if not materialized:
        return ()

    if bounds is None:
        xmin = min(position[0] for position in materialized)
        xmax = max(position[0] for position in materialized)
        ymin = min(position[1] for position in materialized)
        ymax = max(position[1] for position in materialized)
    else:
        if len(bounds) != 4 or not all(isfinite(value) for value in bounds):
            raise ValueError("bounds must contain four finite values.")
        xmin, xmax, ymin, ymax = bounds
        if xmin > xmax or ymin > ymax:
            raise ValueError("bounds must have nonnegative width and height.")

    if bins_per_axis is None:
        bins_per_axis = min(32, max(1, int(ceil(len(materialized) ** 0.5))))
    if type(bins_per_axis) is not int or bins_per_axis < 1:
        raise ValueError("bins_per_axis must be a positive integer.")

    span_x = xmax - xmin
    span_y = ymax - ymin
    bins: dict[tuple[int, int], dict[int, deque[CandidatePosition]]] = {}
    for position in materialized:
        if span_x == 0.0:
            bin_x = 0
        else:
            bin_x = min(
                bins_per_axis - 1,
                max(0, int((position[0] - xmin) / span_x * bins_per_axis)),
            )
        if span_y == 0.0:
            bin_y = 0
        else:
            bin_y = min(
                bins_per_axis - 1,
                max(0, int((position[1] - ymin) / span_y * bins_per_axis)),
            )
        rank = _status_rank(water_status(position) if water_status is not None else "water")
        bins.setdefault((bin_x, bin_y), {}).setdefault(rank, deque()).append(position)

    for grouped in bins.values():
        for rank, values in grouped.items():
            grouped[rank] = deque(sorted(values, key=lambda position: (position[0], position[1])))

    ordered: list[CandidatePosition] = []
    bin_order = _spread_bin_order(bins_per_axis)
    for rank in (0, 1, 2):
        for bin_x, bin_y in bin_order:
            values = bins.get((bin_x, bin_y), {}).get(rank)
            if values:
                ordered.append(values.popleft())
        while True:
            added = False
            for bin_x, bin_y in bin_order:
                values = bins.get((bin_x, bin_y), {}).get(rank)
                if values:
                    ordered.append(values.popleft())
                    added = True
            if not added:
                break
    if len(ordered) != len(materialized):
        raise RuntimeError("candidate ordering lost input positions")
    return tuple(ordered)


def iter_ordered_candidate_positions(
    positions: Iterable[CandidatePosition],
    *,
    water_status: WaterStatusQuery | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    bins_per_axis: int | None = None,
):
    """Yield order_candidate_positions as a stream."""

    yield from order_candidate_positions(
        positions,
        water_status=water_status,
        bounds=bounds,
        bins_per_axis=bins_per_axis,
    )


__all__ = [
    "CandidatePosition",
    "WaterStatus",
    "WaterStatusQuery",
    "iter_ordered_candidate_positions",
    "order_candidate_positions",
]
