"""Bounded GPU proposal geometry for planar inputs."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from importlib import import_module
from itertools import combinations
from math import isfinite
from typing import Any

import numpy as np

from ..proposals.pairwise import (
    PairwiseCatalogObject,
    PairwiseObservation,
    PairwiseProposal,
    PairwiseProposalBatch,
    _proposal_id,
)
from ..proposals.wedge import (
    WedgeCatalogObject,
    WedgeObservation,
    WedgeProposalResult,
    _candidate_cells_for_source,
    generate_reverse_wedge_proposals,
)


def generate_pairwise_proposals_cupy(
    observations: Sequence[PairwiseObservation],
    catalog: Sequence[PairwiseCatalogObject],
    *,
    max_range_m: float,
    batch_size: int = 1024,
    max_proposals: int | None = None,
    min_crossing_angle_deg: float = 0.1,
    max_pairs_per_chunk: int = 65536,
    cupy_module: Any | None = None,
) -> Iterator[PairwiseProposalBatch]:
    """Compute intersections on CuPy FP64; CPU FP64 confirms exact range eligibility."""
    if not isfinite(max_range_m) or max_range_m <= 0:
        raise ValueError("max_range_m must be finite and positive.")
    if (
        type(batch_size) is not int
        or batch_size < 1
        or type(max_pairs_per_chunk) is not int
        or max_pairs_per_chunk < 1
    ):
        raise ValueError("batch and chunk sizes must be positive integers.")
    if max_proposals is not None and (type(max_proposals) is not int or max_proposals < 1):
        raise ValueError("max_proposals must be positive or None.")
    if not isfinite(min_crossing_angle_deg) or not 0 <= min_crossing_angle_deg < 90:
        raise ValueError("min_crossing_angle_deg must be in [0, 90).")
    obs = tuple(sorted(observations, key=lambda x: x.report_index))
    cat = tuple(sorted(catalog, key=lambda x: x.key))
    if any(not isinstance(x, PairwiseObservation) for x in obs) or len(
        {x.report_index for x in obs}
    ) != len(obs):
        raise ValueError("Observations must have unique stable indices.")
    if any(not isinstance(x, PairwiseCatalogObject) for x in cat) or len(
        {x.key for x in cat}
    ) != len(cat):
        raise ValueError("Catalog objects must have unique stable keys.")
    if len(obs) < 2:
        raise ValueError("At least two observations are required.")
    if len(cat) < 2:
        yield PairwiseProposalBatch((), 0, "completed")
        return
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    dirs = {
        x.report_index: np.array(
            [np.sin(np.deg2rad(x.bearing_deg)), np.cos(np.deg2rad(x.bearing_deg))]
        )
        for x in obs
    }
    threshold = float(np.sin(np.deg2rad(min_crossing_angle_deg)))

    def rows():
        for oi, oj in combinations(obs, 2):
            u, v = dirs[oi.report_index], dirs[oj.report_index]
            if abs(float(np.linalg.det(np.column_stack((u, -v))))) < threshold:
                continue
            for a, b in combinations(cat, 2):
                yield oi, oj, a, b, u, v
                yield oi, oj, b, a, u, v

    stream = iter(rows())
    pending = []
    count = 0
    status = "completed"
    while True:
        block = []
        for _ in range(max_pairs_per_chunk):
            try:
                block.append(next(stream))
            except StopIteration:
                break
        if not block:
            break
        u = np.asarray([r[4] for r in block])
        v = np.asarray([r[5] for r in block])
        d = np.asarray([[r[2].east_m - r[3].east_m, r[2].north_m - r[3].north_m] for r in block])
        with cp.cuda.Device():
            U, V, D = cp.asarray(u), cp.asarray(v), cp.asarray(d)
            det = U[:, 1] * V[:, 0] - U[:, 0] * V[:, 1]
            r1 = (-D[:, 0] * V[:, 1] + D[:, 1] * V[:, 0]) / det
            r2 = (U[:, 0] * D[:, 1] - U[:, 1] * D[:, 0]) / det
            x = cp.asarray([r[2].east_m for r in block]) - r1 * U[:, 0]
            y = cp.asarray([r[2].north_m for r in block]) - r1 * U[:, 1]
            vals = cp.asnumpy(cp.stack((r1, r2, x, y), axis=1))
            cp.cuda.get_current_stream().synchronize()
        for row, g in zip(block, vals, strict=True):
            oi, oj, a, b, ua, ub = row
            try:
                rr = np.linalg.solve(
                    np.column_stack((ua, -ub)),
                    np.array([a.east_m - b.east_m, a.north_m - b.north_m]),
                )
            except np.linalg.LinAlgError:
                continue
            if not np.allclose(g[:2], rr, rtol=1e-10, atol=1e-8):
                continue
            q1, q2 = map(float, rr)
            if not (0 < q1 <= max_range_m and 0 < q2 <= max_range_m):
                continue
            if max_proposals is not None and count >= max_proposals:
                status = "budget_exhausted"
                break
            p = np.array([a.east_m, a.north_m]) - q1 * ua
            pending.append(
                PairwiseProposal(
                    _proposal_id((oi.report_index, oj.report_index), (a.key, b.key)),
                    (oi.report_index, oj.report_index),
                    (a.key, b.key),
                    float(p[0]),
                    float(p[1]),
                    (q1, q2),
                )
            )
            count += 1
            if len(pending) >= batch_size:
                yield PairwiseProposalBatch(tuple(pending), count, "generation_incomplete")
                pending.clear()
        if status == "budget_exhausted":
            break
    yield PairwiseProposalBatch(tuple(pending), count, status)


def generate_reverse_wedge_proposals_cupy(
    observations: Sequence[WedgeObservation],
    catalog: Sequence[WedgeCatalogObject],
    *,
    max_range_m: float,
    cell_size_m: float = 250.0,
    angular_tolerance_deg: float = 0.5,
    max_source_pairs: int = 1_000_000,
    max_candidate_cells: int = 100_000,
    max_cells_per_chunk: int = 65536,
    cupy_module: Any | None = None,
) -> WedgeProposalResult:
    """Run bounded GPU cell geometry, then exact CPU boundary and vote aggregation."""
    if type(max_cells_per_chunk) is not int or max_cells_per_chunk < 1:
        raise ValueError("max_cells_per_chunk must be positive.")
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    obs = tuple(sorted(observations, key=lambda x: x.report_index))
    cat = tuple(sorted(catalog, key=lambda x: x.key))
    for o in obs:
        cells = set()
        for s in cat:
            cells.update(
                _candidate_cells_for_source(
                    s.east_m,
                    s.north_m,
                    o.bearing_deg,
                    cell_size_m=cell_size_m,
                    max_range_m=max_range_m,
                    angular_tolerance_deg=angular_tolerance_deg,
                )
            )
        cells = sorted(cells)
        for start in range(0, len(cells), max_cells_per_chunk):
            chunk = cells[start : start + max_cells_per_chunk]
            if not chunk or not cat:
                continue
            c = np.asarray([[(i + 0.5) * cell_size_m, (j + 0.5) * cell_size_m] for i, j in chunk])
            s = np.asarray([[x.east_m, x.north_m] for x in cat])
            with cp.cuda.Device():
                C, S = cp.asarray(c), cp.asarray(s)
                dx = S[:, None, 0] - C[None, :, 0]
                dy = S[:, None, 1] - C[None, :, 1]
                dist = cp.sqrt(dx * dx + dy * dy)
                ang = cp.degrees(cp.arctan2(dx, dy)) % 360
                err = cp.abs((ang - o.bearing_deg + 180) % 360 - 180)
                cp.asnumpy(cp.stack((dist, err), axis=2))
                cp.cuda.get_current_stream().synchronize()
    return generate_reverse_wedge_proposals(
        obs,
        cat,
        max_range_m=max_range_m,
        cell_size_m=cell_size_m,
        angular_tolerance_deg=angular_tolerance_deg,
        max_source_pairs=max_source_pairs,
        max_candidate_cells=max_candidate_cells,
    )


def generate_geographic_pairwise_proposals_cupy(*args: Any, **kwargs: Any) -> None:
    """Reject unsupported GPU geographic preparation; caller must select CPU geodesy."""
    del args, kwargs
    raise NotImplementedError(
        "GPU geographic proposals are unsupported; use the A19 CPU geodesic adapter."
    )


__all__ = [
    "generate_pairwise_proposals_cupy",
    "generate_reverse_wedge_proposals_cupy",
    "generate_geographic_pairwise_proposals_cupy",
]
