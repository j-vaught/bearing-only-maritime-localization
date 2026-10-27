"""Batched GPU matching for positive chart-marker detections.

Only edges inside the caller-provided hard bearing tolerance are allowed.
The objective first maximizes the number of matched detections and then
minimizes total absolute wrapped bearing residual. Undetected chart aids have
zero cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

from .batch_assignment import assign_partial_cost_matrices_rawkernel

MarkerCandidateStatus = Literal["complete", "infeasible", "unresolved"]


@dataclass(frozen=True, slots=True)
class BatchedMarkerMatchingResult:
    """Per-candidate injective matches and device-resident residual totals."""

    matches: tuple[tuple[tuple[int, int], ...], ...]
    status: tuple[MarkerCandidateStatus, ...]
    complete: tuple[bool, ...]
    residuals_deg: Any
    possible_complete: tuple[bool, ...]


def match_positive_markers_cupy(
    predicted_bearings_deg: Any,
    group_bearings_deg: Any,
    tolerances_deg: Any,
    *,
    valid_detections: Any | None = None,
    valid_groups: Any | None = None,
    valid_edges: Any | None = None,
    unresolved_edges: Any | None = None,
    cupy_module: Any | None = None,
) -> BatchedMarkerMatchingResult:
    """Match batched detections to distinct predicted groups on a CuPy device.

    Bearing arrays have shape ``(batch, detections)`` and ``(batch, groups)``.
    Tolerances broadcast from a scalar, a ``(detections,)`` vector, or a full
    detection array. Validity masks are optional and broadcastable to their
    corresponding arrays. ``valid_edges`` further restricts definite matches;
    ``unresolved_edges`` marks edges whose compatibility is not certified.

    The solver returns a maximum-cardinality assignment, minimizing the sum of
    absolute wrapped residuals among assignments of that cardinality. Complete
    assignments therefore always outrank partial assignments. No finite penalty
    is added for unused groups. If uncertain edges could enable completeness,
    or any uncertain edge could change the optimum, status is ``unresolved``.
    No CPU fallback is attempted; missing CuPy or a usable device is an error.
    """
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    detections = cp.asarray(predicted_bearings_deg, dtype=cp.float64)
    groups = cp.asarray(group_bearings_deg, dtype=cp.float64)
    if detections.ndim != 2 or groups.ndim != 2 or detections.shape[0] != groups.shape[0]:
        raise ValueError("bearing inputs must be 2D arrays with a common batch dimension.")
    batch, rows = map(int, detections.shape)
    padded_groups = int(groups.shape[1])
    tol = cp.asarray(tolerances_deg, dtype=cp.float64)
    try:
        tol = cp.broadcast_to(tol, detections.shape)
    except ValueError as exc:
        raise ValueError("tolerances must broadcast to the detection shape.") from exc

    detection_mask = _mask(cp, valid_detections, detections.shape, True, "valid_detections")
    group_mask = _mask(cp, valid_groups, groups.shape, True, "valid_groups")
    if unresolved_edges is None:
        uncertain = cp.zeros((batch, rows, padded_groups), dtype=cp.bool_)
    else:
        uncertain = _mask(
            cp, unresolved_edges, (batch, rows, padded_groups), False, unresolved_edges
        )
    if valid_edges is None:
        edge_mask = ~uncertain
    else:
        edge_mask = _mask(cp, valid_edges, (batch, rows, padded_groups), False, valid_edges)
    if bool((edge_mask & uncertain).any().item()):
        raise ValueError("an edge cannot be both valid and unresolved.")
    if bool(
        (detection_mask & (~cp.isfinite(detections) | (detections < 0) | (detections >= 360)))
        .any()
        .item()
    ):
        raise ValueError("valid predicted bearings must be finite and in [0, 360).")
    if bool((group_mask & (~cp.isfinite(groups) | (groups < 0) | (groups >= 360))).any().item()):
        raise ValueError("valid group bearings must be finite and in [0, 360).")
    if bool((detection_mask & (~cp.isfinite(tol) | (tol < 0) | (tol > 180))).any().item()):
        raise ValueError("valid detection tolerances must be finite and in [0, 180].")

    raw_delta = cp.abs(detections[:, :, None] - groups[:, None, :])
    wrapped = cp.minimum(raw_delta, 360.0 - raw_delta)
    possible = (
        detection_mask[:, :, None]
        & group_mask[:, None, :]
        & cp.isfinite(wrapped)
        & (wrapped <= tol[:, :, None])
    )
    definite = possible & edge_mask
    # Unresolved edges are conservative possibilities and may represent bearing
    # uncertainty large enough to move a nominally out-of-tolerance pair inside.
    uncertain = uncertain & detection_mask[:, :, None] & group_mask[:, None, :]
    costs = cp.where(definite, wrapped, cp.inf)
    # One omitted report must cost more than the largest possible residual sum.
    # The strict margin preserves cardinality priority for every tolerance.
    miss_penalty = float(180 * rows + 1)
    miss_cost = cp.full((batch, rows), miss_penalty, dtype=cp.float64)
    group_cost = cp.zeros((batch, padded_groups), dtype=cp.float64)

    base = assign_partial_cost_matrices_rawkernel(
        costs,
        miss_cost,
        group_cost,
        group_counts=cp.full((batch,), padded_groups, dtype=cp.int64),
        cupy_module=cp,
    )
    # Masked and padded groups are forbidden edges; unused groups cost zero.
    host_matches = base.matches
    host_complete = tuple(
        len(item) == int(cp.sum(detection_mask[b]).item()) for b, item in enumerate(host_matches)
    )
    statuses: list[MarkerCandidateStatus] = []
    if bool(uncertain.any().item()):
        possible_costs = cp.where(definite, wrapped, cp.where(uncertain, 0.0, cp.inf))
        optimistic = assign_partial_cost_matrices_rawkernel(
            possible_costs,
            miss_cost,
            group_cost,
            group_counts=cp.full((batch,), padded_groups, dtype=cp.int64),
            cupy_module=cp,
        )
        possible_complete = tuple(
            len(item) == int(cp.sum(detection_mask[b]).item())
            for b, item in enumerate(optimistic.matches)
        )
        uncertain_host = cp.asnumpy(uncertain)
        for b in range(batch):
            # Any live uncertain edge could alter the residual optimum or its
            # uniqueness, even when a definite complete assignment exists.
            uncertain_relevant = bool(uncertain_host[b].any())
            statuses.append(
                "unresolved"
                if uncertain_relevant or (not host_complete[b] and possible_complete[b])
                else "complete"
                if host_complete[b]
                else "infeasible"
            )
    else:
        possible_complete = host_complete
        for value in host_complete:
            statuses.append("complete" if value else "infeasible")

    # Recompute the returned objective from selected definite edges. This avoids
    # exposing the internal cardinality penalty as a residual.
    residuals = cp.zeros((batch,), dtype=cp.float64)
    for b, pairs in enumerate(host_matches):
        if pairs:
            rr = cp.asarray([r for r, _ in pairs], dtype=cp.int64)
            gg = cp.asarray([g for _, g in pairs], dtype=cp.int64)
            residuals[b] = cp.sum(wrapped[b, rr, gg], dtype=cp.float64)
    # Invalid and padded rows have no finite edges, so they are absent from matches.
    return BatchedMarkerMatchingResult(
        host_matches, tuple(statuses), host_complete, residuals, possible_complete
    )


def _mask(cp: Any, value: Any | None, shape: tuple[int, ...], default: bool, name: str) -> Any:
    if value is None:
        return cp.full(shape, default, dtype=cp.bool_)
    array = cp.asarray(value, dtype=cp.bool_)
    try:
        return cp.broadcast_to(array, shape)
    except ValueError as exc:
        raise ValueError(f"{name} must broadcast to shape {shape}.") from exc


__all__ = ["BatchedMarkerMatchingResult", "match_positive_markers_cupy"]
