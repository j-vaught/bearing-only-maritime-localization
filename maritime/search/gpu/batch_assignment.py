"""Exact batched partial one-to-one assignment scored on a CuPy device.

The solver applies a rectangular Hungarian algorithm to each candidate matrix.
Input costs and objective accumulation remain device arrays; only final integer
match indices are copied to the host because Python result tuples are metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class BatchedAssignmentResult:
    """Assignments and device-resident objective decompositions."""

    matches: tuple[tuple[tuple[int, int], ...], ...]
    unmatched_reports: tuple[tuple[int, ...], ...]
    unused_groups: tuple[tuple[int, ...], ...]
    device_costs: Any
    baseline_constants: Any
    matched_costs: Any
    unmatched_report_costs: Any
    unused_group_costs: Any


def assign_partial_cost_matrices_cupy(
    matched_costs: Any,
    report_clutter_costs: Any,
    group_miss_costs: Any,
    *,
    baseline_constants: Any = 0.0,
    group_counts: Any | None = None,
    cupy_module: Any | None = None,
) -> BatchedAssignmentResult:
    """Solve a batch of optional injective assignments exactly on a CuPy device.

    matched_costs has shape (batch, reports, padded_groups). The other
    arrays have shapes (batch, reports) and (batch, padded_groups);
    scalar report, group, and baseline inputs broadcast across the batch and
    relevant dimension. group_counts optionally marks a valid prefix of
    group columns for each candidate, allowing different logical group counts.
    Positive infinity forbids a match edge; NaN and negative infinity are
    rejected. Finite negative costs and all unmatched/missed/baseline terms are
    preserved in the complete objective.

    The exact Hungarian algorithm uses CuPy arrays for its potentials, reduced
    costs, and augmenting path state. Batch members execute sequentially in the
    host controller, so this implementation prioritizes exact device arithmetic
    and a bounded API; it does not claim parallel speedup over CPU solvers.
    Tied optima may produce a different optimal matching than SciPy, while the
    returned matching is repeatable and the objective remains exact.
    """
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    matched = cp.asarray(matched_costs, dtype=cp.float64)
    if matched.ndim != 3:
        raise ValueError("matched_costs must have shape (batch, reports, groups).")
    batch, report_count, padded_groups = map(int, matched.shape)
    if bool(cp.isnan(matched).any().item()) or bool(cp.isneginf(matched).any().item()):
        raise ValueError("matched costs must be finite or positive infinity.")

    report_miss = _broadcast_vector(
        cp, report_clutter_costs, batch, report_count, "report_clutter_costs"
    )
    group_miss = _broadcast_vector(cp, group_miss_costs, batch, padded_groups, "group_miss_costs")
    baselines = _broadcast_batch(cp, baseline_constants, batch, "baseline_constants")
    if not bool(cp.isfinite(report_miss).all().item()) or not bool(
        cp.isfinite(group_miss).all().item()
    ):
        raise ValueError("unmatched costs must be finite.")
    if not bool(cp.isfinite(baselines).all().item()):
        raise ValueError("baseline_constants must be finite.")

    if group_counts is None:
        valid_groups = [padded_groups] * batch
    else:
        counts = cp.asarray(group_counts)
        if counts.shape != (batch,) or counts.dtype.kind not in "iu":
            raise ValueError("group_counts must be an integer vector of shape (batch,).")
        valid_groups = [int(x) for x in cp.asnumpy(counts)]
        if any(n < 0 or n > padded_groups for n in valid_groups):
            raise ValueError("group_counts entries must be in [0, padded_groups].")

    n = report_count
    if batch == 0:
        empty = cp.empty(0, dtype=cp.float64)
        return BatchedAssignmentResult((), (), (), empty, empty, empty, empty, empty)
    counts = cp.asarray(valid_groups, dtype=cp.int64)
    m = padded_groups + n
    delta_costs = matched - report_miss[:, :, None] - group_miss[:, None, :]
    finite_edges = cp.isfinite(matched)
    if bool(cp.isnan(delta_costs).any().item()) or bool(
        (finite_edges & ~cp.isfinite(delta_costs)).any().item()
    ):
        raise OverflowError("finite input costs overflowed while forming assignment deltas.")
    vals = cp.abs(delta_costs[cp.isfinite(delta_costs)])
    scale = float(cp.max(vals).item()) if int(vals.size) else 0.0
    sentinel = (2.0 * n + 1.0) * scale + 1.0
    if not isfinite(sentinel):
        raise OverflowError("cost magnitudes are too large for a finite forbidden-edge sentinel.")
    valid_edge = cp.arange(padded_groups)[None, None, :] < counts[:, None, None]
    cost = cp.full((batch, n, m), sentinel, dtype=cp.float64)
    if padded_groups:
        cost[:, :, :padded_groups] = cp.where(
            cp.isfinite(delta_costs) & valid_edge, delta_costs, sentinel
        )
    rows = cp.arange(n)
    cost[:, rows, padded_groups + rows] = 0.0
    u = cp.zeros((batch, n + 1), dtype=cp.float64)
    v = cp.zeros((batch, m + 1), dtype=cp.float64)
    p = cp.zeros((batch, m + 1), dtype=cp.int64)
    way = cp.zeros((batch, m + 1), dtype=cp.int64)
    bids = cp.arange(batch)
    col_ids = cp.arange(1, m + 1)
    row_ids = cp.arange(1, n + 1)
    for i in range(1, n + 1):
        p[:, 0] = i
        j0 = cp.zeros(batch, dtype=cp.int64)
        minv = cp.full((batch, m + 1), cp.inf, dtype=cp.float64)
        used = cp.zeros((batch, m + 1), dtype=cp.bool_)
        active = cp.ones(batch, dtype=cp.bool_)
        while bool(active.any().item()):
            used[bids[active], j0[active]] = True
            i0 = p[bids, j0]
            cur = cost[bids, i0 - 1, :] - u[bids, i0, None] - v[:, 1:]
            available = ~used[:, 1:] & active[:, None]
            better = available & (cur < minv[:, 1:])
            minv[:, 1:] = cp.where(better, cur, minv[:, 1:])
            way[:, 1:] = cp.where(better, j0[:, None], way[:, 1:])
            j1 = cp.argmin(cp.where(available, minv[:, 1:], cp.inf), axis=1).astype(cp.int64) + 1
            step = cp.where(active, minv[bids, j1], 0.0)
            assigned_rows = p[:, 1:] - 1
            used_rows = (
                cp.sum(
                    (used[:, :, None] & (p[:, :, None] == row_ids[None, None, :])).astype(cp.int32),
                    axis=1,
                )
                > 0
            )
            u[:, 1:] += used_rows * step[:, None]
            v[:, 1:] -= used[:, 1:] * step[:, None]
            minv[:, 1:] = cp.where(used[:, 1:], minv[:, 1:], minv[:, 1:] - step[:, None])
            j0 = cp.where(active, j1, j0)
            active &= p[bids, j0] != 0
        path = j0 > 0
        while bool(path.any().item()):
            j1 = way[bids, j0]
            p[bids[path], j0[path]] = p[bids[path], j1[path]]
            j0 = cp.where(path, j1, j0)
            path &= j0 > 0
    assigned_rows = p[:, 1:] - 1
    assigned_cols = cp.broadcast_to(col_ids[None, :] - 1, (batch, m))
    row_to_col = cp.full((batch, n), -1, dtype=cp.int64)
    valid_assignment = assigned_rows >= 0
    row_to_col[
        cp.broadcast_to(bids[:, None], (batch, m))[valid_assignment],
        assigned_rows[valid_assignment],
    ] = assigned_cols[valid_assignment]
    candidate_rows = cp.broadcast_to(cp.arange(n)[None, :], (batch, n))
    safe_cols = cp.maximum(row_to_col, 0)
    if padded_groups:
        selected_finite = cp.isfinite(matched[bids[:, None], candidate_rows, safe_cols])
        matches_mask = (row_to_col >= 0) & (row_to_col < counts[:, None]) & selected_finite
    else:
        matches_mask = cp.zeros((batch, n), dtype=cp.bool_)
    host_cols = cp.asnumpy(row_to_col)
    host_mask = cp.asnumpy(matches_mask)
    all_matches, all_unmatched, all_unused = [], [], []
    for b in range(batch):
        pairs = tuple((r, int(host_cols[b, r])) for r in range(n) if host_mask[b, r])
        all_matches.append(pairs)
        all_unmatched.append(tuple(r for r in range(n) if not host_mask[b, r]))
        used_groups = {c for _, c in pairs}
        all_unused.append(tuple(c for c in range(valid_groups[b]) if c not in used_groups))
    if padded_groups:
        gathered = matched[bids[:, None], candidate_rows, safe_cols]
        matched_terms = cp.sum(cp.where(matches_mask, gathered, 0.0), axis=1)
    else:
        matched_terms = cp.zeros(batch, dtype=cp.float64)
    unmatched_terms = cp.sum(cp.where(~matches_mask, report_miss, 0.0), axis=1)
    group_ids = cp.arange(padded_groups)[None, :]
    used_groups = cp.any(
        matches_mask[:, :, None] & (row_to_col[:, :, None] == group_ids[:, None, :]), axis=1
    )
    unused_terms = cp.sum(
        cp.where((group_ids < counts[:, None]) & ~used_groups, group_miss, 0.0), axis=1
    )
    totals = baselines + matched_terms + unmatched_terms + unused_terms
    if not bool(cp.isfinite(totals).all().item()):
        raise OverflowError("assignment objective overflowed float64.")
    return BatchedAssignmentResult(
        tuple(all_matches),
        tuple(all_unmatched),
        tuple(all_unused),
        totals,
        baselines,
        matched_terms,
        unmatched_terms,
        unused_terms,
    )


_RAW_HUNGARIAN_SOURCE = r"""
extern "C" __global__ void batched_hungarian(
    const double* matched, const double* report_miss, const double* group_miss,
    const double* baselines, const int* counts, int B, int R, int G,
    double* u_all, double* v_all, int* p_all, int* way_all,
    double* minv_all, unsigned char* used_all, int* row_cols,
    double* matched_sum, double* unmatched_sum, double* unused_sum,
    double* total, int* error
) {
    const int b = blockIdx.x;
    if (b >= B || threadIdx.x != 0) return;
    const int M = G + R;
    const int U = R + 1;
    const int V = M + 1;
    double* u = u_all + b * U;
    double* v = v_all + b * V;
    int* p = p_all + b * V;
    int* way = way_all + b * V;
    double* minv = minv_all + b * V;
    unsigned char* used = used_all + b * V;
    const int ng = counts[b];
    double scale = 0.0;
    for (int r = 0; r < R; ++r) {
        for (int c = 0; c < ng; ++c) {
            double edge = matched[(b * R + r) * G + c];
            if (isfinite(edge)) {
                double d = edge - report_miss[b * R + r] - group_miss[b * G + c];
                if (!isfinite(d)) { error[b] = 1; return; }
                double a = fabs(d);
                if (a > scale) scale = a;
            }
        }
    }
    double sentinel = (2.0 * R + 1.0) * scale + 1.0;
    if (!isfinite(sentinel)) { error[b] = 2; return; }
    for (int k = 0; k < U; ++k) u[k] = 0.0;
    for (int k = 0; k < V; ++k) { v[k] = 0.0; p[k] = 0; way[k] = 0; }
    for (int i = 1; i <= R; ++i) {
        p[0] = i;
        int j0 = 0;
        for (int j = 0; j <= M; ++j) { minv[j] = 1.7976931348623157e+308; used[j] = 0; }
        while (true) {
            used[j0] = 1;
            int i0 = p[j0];
            double delta = 1.7976931348623157e+308;
            int j1 = 0;
            for (int j = 1; j <= M; ++j) {
                if (used[j]) continue;
                int r = i0 - 1;
                int c = j - 1;
                double a;
                if (c < G) {
                    if (c < ng) {
                        double edge = matched[(b * R + r) * G + c];
                        a = isfinite(edge) ? edge - report_miss[b * R + r]
                                                   - group_miss[b * G + c] : sentinel;
                    } else a = sentinel;
                } else {
                    a = (c == G + r) ? 0.0 : sentinel;
                }
                double cur = a - u[i0] - v[j];
                if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
                if (minv[j] < delta) { delta = minv[j]; j1 = j; }
            }
            if (!isfinite(delta) || j1 == 0) { error[b] = 3; return; }
            for (int j = 0; j <= M; ++j) {
                if (used[j]) { u[p[j]] += delta; v[j] -= delta; }
                else minv[j] -= delta;
            }
            j0 = j1;
            if (p[j0] == 0) break;
        }
        do {
            int j1 = way[j0];
            p[j0] = p[j1];
            j0 = j1;
        } while (j0 != 0);
    }
    for (int r = 0; r < R; ++r) row_cols[b * R + r] = -1;
    for (int j = 1; j <= M; ++j) if (p[j] > 0) row_cols[b * R + p[j] - 1] = j - 1;
    double mc = 0.0, rc = 0.0, gc = 0.0;
    for (int r = 0; r < R; ++r) {
        int c = row_cols[b * R + r];
        if (c >= 0 && c < ng && isfinite(matched[(b * R + r) * G + c])) {
            mc += matched[(b * R + r) * G + c];
        } else {
            row_cols[b * R + r] = -1;
            rc += report_miss[b * R + r];
        }
    }
    for (int c = 0; c < ng; ++c) {
        bool found = false;
        for (int r = 0; r < R; ++r) if (row_cols[b * R + r] == c) found = true;
        if (!found) gc += group_miss[b * G + c];
    }
    matched_sum[b] = mc;
    unmatched_sum[b] = rc;
    unused_sum[b] = gc;
    total[b] = baselines[b] + mc + rc + gc;
    if (!isfinite(total[b])) error[b] = 4;
}
"""


def assign_partial_cost_matrices_rawkernel(
    matched_costs: Any,
    report_clutter_costs: Any,
    group_miss_costs: Any,
    *,
    baseline_constants: Any = 0.0,
    group_counts: Any | None = None,
    cupy_module: Any | None = None,
) -> BatchedAssignmentResult:
    """CUDA RawKernel variant with one candidate per block and no loop sync."""
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    matched = cp.asarray(matched_costs, dtype=cp.float64)
    if matched.ndim != 3:
        raise ValueError("matched_costs must have shape (batch, reports, groups).")
    batch, n, groups = map(int, matched.shape)
    if bool(cp.isnan(matched).any().item()) or bool(cp.isneginf(matched).any().item()):
        raise ValueError("matched costs must be finite or positive infinity.")
    reports = _broadcast_vector(cp, report_clutter_costs, batch, n, "report_clutter_costs")
    misses = _broadcast_vector(cp, group_miss_costs, batch, groups, "group_miss_costs")
    baselines = _broadcast_batch(cp, baseline_constants, batch, "baseline_constants")
    if not bool(cp.isfinite(reports).all().item()) or not bool(cp.isfinite(misses).all().item()):
        raise ValueError("unmatched costs must be finite.")
    if not bool(cp.isfinite(baselines).all().item()):
        raise ValueError("baseline_constants must be finite.")
    if group_counts is None:
        counts = cp.full(batch, groups, dtype=cp.int32)
    else:
        counts64 = cp.asarray(group_counts)
        if counts64.shape != (batch,) or counts64.dtype.kind not in "iu":
            raise ValueError("group_counts must be an integer vector of shape (batch,).")
        counts_host = cp.asnumpy(counts64)
        if np.any(counts_host < 0) or np.any(counts_host > groups):
            raise ValueError("group_counts entries must be in [0, padded_groups].")
        counts = counts64.astype(cp.int32, copy=False)
    totals = cp.empty(batch, dtype=cp.float64)
    msum, rsum, gsum = (cp.empty(batch, dtype=cp.float64) for _ in range(3))
    cols = cp.empty((batch, n), dtype=cp.int32)
    errors = cp.zeros(batch, dtype=cp.int32)
    # Workspace is per candidate and reused by sequential augmentations.
    u = cp.empty((batch, n + 1), dtype=cp.float64)
    v = cp.empty((batch, groups + n + 1), dtype=cp.float64)
    p = cp.empty((batch, groups + n + 1), dtype=cp.int32)
    way = cp.empty_like(p)
    minv = cp.empty((batch, groups + n + 1), dtype=cp.float64)
    used = cp.empty((batch, groups + n + 1), dtype=cp.uint8)
    if batch:
        kernel = cp.RawKernel(_RAW_HUNGARIAN_SOURCE, "batched_hungarian")
        kernel(
            (batch,),
            (1,),
            (
                matched,
                reports,
                misses,
                baselines,
                counts,
                np.int32(batch),
                np.int32(n),
                np.int32(groups),
                u,
                v,
                p,
                way,
                minv,
                used,
                cols,
                msum,
                rsum,
                gsum,
                totals,
                errors,
            ),
        )
        if bool((errors != 0).any().item()):
            code = int(cp.max(errors).item())
            if code in (1, 2):
                raise OverflowError("finite input costs overflowed the CUDA Hungarian solver.")
            raise RuntimeError(f"CUDA Hungarian solver failed with device error {code}.")
    host_cols = cp.asnumpy(cols)
    count_host = cp.asnumpy(counts)
    matches, unmatched, unused = [], [], []
    for b in range(batch):
        pairs = tuple((r, int(c)) for r, c in enumerate(host_cols[b]) if 0 <= c < count_host[b])
        used_groups = {c for _, c in pairs}
        matches.append(pairs)
        unmatched.append(tuple(r for r in range(n) if r not in {i for i, _ in pairs}))
        unused.append(tuple(c for c in range(int(count_host[b])) if c not in used_groups))
    return BatchedAssignmentResult(
        tuple(matches), tuple(unmatched), tuple(unused), totals, baselines, msum, rsum, gsum
    )


def _broadcast_vector(cp: Any, value: Any, batch: int, width: int, name: str) -> Any:
    arr = cp.asarray(value, dtype=cp.float64)
    if arr.ndim == 0:
        return cp.full((batch, width), arr, dtype=cp.float64)
    if arr.shape == (width,):
        return cp.broadcast_to(arr, (batch, width))
    if arr.shape == (batch, width):
        return arr
    raise ValueError(f"{name} must be scalar, ({width},), or ({batch}, {width}).")


def _broadcast_batch(cp: Any, value: Any, batch: int, name: str) -> Any:
    arr = cp.asarray(value, dtype=cp.float64)
    if arr.ndim == 0:
        return cp.full((batch,), arr, dtype=cp.float64)
    if arr.shape != (batch,):
        raise ValueError(f"{name} must be scalar or shape ({batch},).")
    return arr


__all__ = [
    "BatchedAssignmentResult",
    "assign_partial_cost_matrices_cupy",
    "assign_partial_cost_matrices_rawkernel",
]
