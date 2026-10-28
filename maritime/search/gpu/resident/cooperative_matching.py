"""Exact warp-cooperative matching of hard-gated positive marker costs.

Each candidate uses one CUDA warp. Actual group counts, rather than padded
capacity, determine the Hungarian column loop. No Python per-candidate solver,
assignment reconstruction, host copy or CPU fallback occurs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class DeviceMarkerAssignment:
    """Device-resident result arrays; consumers must inspect errors."""

    columns: Any
    matched_counts: Any
    residuals: Any
    errors: Any

    @property
    def complete(self) -> Any:
        return (self.matched_counts == self.columns.shape[1]) & (self.errors == 0)


def solve_cooperative_marker_costs(
    matched_costs: Any,
    group_counts: Any | None = None,
    *,
    cupy_module: Any | None = None,
) -> DeviceMarkerAssignment:
    """Maximize cardinality, then minimize nonnegative residual sum, on CUDA.

    Costs have shape (batch, reports, padded_groups). Positive infinity forbids
    an edge. Finite nonnegative costs are supported; NaN, negative values or
    invalid counts produce a nonzero per-candidate device error. Padded values
    beyond a candidate's count are never inspected. Unused groups cost zero.
    There are no report-specific penalties: each unmatched report has a
    dominating dummy cost calculated from that candidate's finite cost range.

    Each report maps to a distinct group or its own dummy. Returned columns are
    original group-column indices, or -1 for unmatched reports. The residual
    contains only real matched edges. A no-group candidate returns immediately.
    Ties choose the lowest logical column during deterministic warp reductions.

    Error codes are 1 for invalid count, 2 for invalid finite-edge data, 3 for
    objective overflow, 4 for an augmenting-path failure, 5 for sum overflow.
    These outputs stay on the selected current CUDA device; no error or result
    scalar is implicitly copied to the host.
    """
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    costs = cp.ascontiguousarray(matched_costs, dtype=cp.float64)
    if costs.ndim != 3:
        raise ValueError("matched_costs must have shape (batch, reports, groups).")
    batch, reports, groups = map(int, costs.shape)
    if reports + groups >= 2**31 or batch >= 2**31:
        raise ValueError("Matching dimensions exceed the CUDA int32 index contract.")
    if group_counts is None:
        counts = cp.full(batch, groups, dtype=cp.int64)
    else:
        source_counts = cp.asarray(group_counts)
        if source_counts.shape != (batch,) or source_counts.dtype.kind not in "iu":
            raise ValueError("group_counts must be an integer vector of shape (batch,).")
        counts = cp.ascontiguousarray(source_counts, dtype=cp.int64)
    columns = cp.empty((batch, reports), dtype=cp.int32)
    matched_counts = cp.empty(batch, dtype=cp.int32)
    residuals = cp.empty(batch, dtype=cp.float64)
    errors = cp.empty(batch, dtype=cp.int32)
    if batch:
        width = groups + reports + 1
        u = cp.empty((batch, reports + 1), dtype=cp.float64)
        v = cp.empty((batch, width), dtype=cp.float64)
        p = cp.empty((batch, width), dtype=cp.int32)
        way = cp.empty_like(p)
        minv = cp.empty_like(v)
        used = cp.empty((batch, width), dtype=cp.uint8)
        source = Path(__file__).with_suffix(".cu").read_text()
        kernel = cp.RawKernel(source, "cooperative_marker_hungarian", options=("--std=c++11",))
        kernel(
            (batch,),
            (32,),
            (
                costs,
                counts,
                np.int32(batch),
                np.int32(reports),
                np.int32(groups),
                u,
                v,
                p,
                way,
                minv,
                used,
                columns,
                matched_counts,
                residuals,
                errors,
            ),
        )
    return DeviceMarkerAssignment(columns, matched_counts, residuals, errors)
