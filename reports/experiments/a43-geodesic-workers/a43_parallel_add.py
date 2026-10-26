from pathlib import Path
p=Path('bearing_localization/search/proposals/geodesic_wedge.py')
s=p.read_text()
s=s.replace('from collections.abc import Mapping, Sequence\n', 'from collections import deque\nfrom collections.abc import Mapping, Sequence\nfrom concurrent.futures import Future, ProcessPoolExecutor, TimeoutError as FutureTimeoutError\n', 1)
s=s.replace('from math import asin, ceil, degrees, isfinite\n', 'from math import asin, ceil, degrees, isfinite\nfrom multiprocessing import get_context\n', 1)
s=s.replace('from pyproj import Geod, Transformer\n', 'from pyproj import CRS, Geod, Transformer\n', 1)
s=s.replace('    max_solver_residual_deg: float\n', '    max_solver_residual_deg: float\n    source_pairs_computed: int\n    source_pairs_discarded: int\n', 1)
marker='''def generate_geographic_reverse_wedge_proposals_geodesic(
'''
helpers='''@dataclass(frozen=True, slots=True)
class _SourceChunkTask:
    source_start: int
    source_chunk: NDArray[np.float64]
    bearing_deg: float
    sigma_deg: float
    sample_distances: NDArray[np.float64]
    angular_offsets_deg: NDArray[np.float64]
    solver_tolerance_deg: float
    cell_size_m: float
    angular_tolerance_deg: float
    final_distance_m: float


@dataclass(frozen=True, slots=True)
class _PairEvidenceResult:
    source_index: int
    pair_evidence: dict[tuple[int, int], tuple[float, float]]
    failed: bool
    samples_total: int
    samples_failed: int
    samples_degenerate: int
    max_solver_residual_deg: float


@dataclass(frozen=True, slots=True)
class _SourceChunkResult:
    pairs: tuple[_PairEvidenceResult, ...]
    source_pairs_failed: int
    geodesic_samples_total: int
    geodesic_samples_failed: int
    geodesic_samples_degenerate: int
    max_solver_residual_deg: float


_WORKER_FORWARD: Transformer | None = None
_WORKER_INVERSE: Transformer | None = None
_WORKER_ORIGIN_X = 0.0
_WORKER_ORIGIN_Y = 0.0
_SOURCE_WORKER_TIMEOUT_SECONDS = 300.0


def _initialize_source_worker(
    projected_crs_wkt: str, origin_lonlat: tuple[float, float]
) -> None:
    """Initialize one spawned worker with immutable frame transforms."""
    global _WORKER_FORWARD, _WORKER_INVERSE, _WORKER_ORIGIN_X, _WORKER_ORIGIN_Y
    projected_crs = CRS.from_wkt(projected_crs_wkt)
    _WORKER_FORWARD = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    _WORKER_INVERSE = Transformer.from_crs(projected_crs, "EPSG:4326", always_xy=True)
    _WORKER_ORIGIN_X, _WORKER_ORIGIN_Y = _WORKER_FORWARD.transform(*origin_lonlat)


def _compute_source_chunk(
    task: _SourceChunkTask,
    forward: Transformer,
    inverse: Transformer,
    origin_x: float,
    origin_y: float,
) -> _SourceChunkResult:
    """Return exact per-source evidence without combining source witnesses."""
    from maritime_localization_shared.geospatial.reverse_bearing import (
        solve_reverse_bearing_observer,
    )

    desired = (task.bearing_deg + task.angular_offsets_deg[None, :, None]) % 360.0
    ranges = task.sample_distances[None, None, :]
    solved = solve_reverse_bearing_observer(
        task.source_chunk[:, None, None, :],
        desired,
        ranges,
        tolerance_deg=task.solver_tolerance_deg,
    )
    points = np.asarray(solved.observer_lonlat_deg, dtype=np.float64)
    converged = np.asarray(solved.converged, dtype=bool)
    degenerate = np.asarray(solved.degenerate, dtype=bool)
    finite_points = np.all(np.isfinite(points), axis=-1)
    valid = finite_points & (converged | degenerate)
    failed_mask = (~converged) & (~degenerate)

    projected_x, projected_y = forward.transform(points[..., 0], points[..., 1])
    local_x = np.asarray(projected_x, dtype=np.float64) - origin_x
    local_y = np.asarray(projected_y, dtype=np.float64) - origin_y
    samples_per_pair = int(points.shape[1] * points.shape[2])
    pairs: list[_PairEvidenceResult] = []
    for local_row, source_index in enumerate(
        range(task.source_start, task.source_start + task.source_chunk.shape[0])
    ):
        cells = _neighbor_cell_indices(
            local_x[local_row], local_y[local_row], valid[local_row], task.cell_size_m
        )
        pair_evidence: dict[tuple[int, int], tuple[float, float]] = {}
        if cells.size:
            cell_i_values = cells[:, 0]
            cell_j_values = cells[:, 1]
            center_x = (cell_i_values + 0.5) * task.cell_size_m + origin_x
            center_y = (cell_j_values + 0.5) * task.cell_size_m + origin_y
            center_lon, center_lat = inverse.transform(center_x, center_y)
            source_lon, source_lat = task.source_chunk[local_row]
            accepted, residuals, evidences = _cell_residual_and_evidence_batch(
                source_lon_deg=float(source_lon),
                source_lat_deg=float(source_lat),
                cell_lon_deg=np.asarray(center_lon, dtype=np.float64),
                cell_lat_deg=np.asarray(center_lat, dtype=np.float64),
                desired_bearing_deg=task.bearing_deg,
                cell_size_m=task.cell_size_m,
                angular_tolerance_deg=task.angular_tolerance_deg,
                sigma_deg=task.sigma_deg,
                final_distance_m=task.final_distance_m,
            )
            for index in np.flatnonzero(accepted):
                cell = (int(cell_i_values[index]), int(cell_j_values[index]))
                pair_evidence[cell] = (float(residuals[index]), float(evidences[index]))
        pair_failed_mask = failed_mask[local_row]
        pairs.append(
            _PairEvidenceResult(
                source_index=source_index,
                pair_evidence=pair_evidence,
                failed=bool(np.any(pair_failed_mask)),
                samples_total=samples_per_pair,
                samples_failed=int(np.count_nonzero(pair_failed_mask)),
                samples_degenerate=int(np.count_nonzero(degenerate[local_row])),
                max_solver_residual_deg=float(
                    np.max(np.abs(solved.angular_residual_deg[local_row]), initial=0.0)
                ),
            )
        )
    return _SourceChunkResult(
        pairs=tuple(pairs),
        source_pairs_failed=sum(pair.failed for pair in pairs),
        geodesic_samples_total=sum(pair.samples_total for pair in pairs),
        geodesic_samples_failed=sum(pair.samples_failed for pair in pairs),
        geodesic_samples_degenerate=sum(pair.samples_degenerate for pair in pairs),
        max_solver_residual_deg=max(
            (pair.max_solver_residual_deg for pair in pairs), default=0.0
        ),
    )


def _compute_source_chunk_in_worker(task: _SourceChunkTask) -> _SourceChunkResult:
    """Compute one chunk using transforms initialized in a spawned worker."""
    if _WORKER_FORWARD is None or _WORKER_INVERSE is None:
        raise RuntimeError("Geodesic source worker was not initialized.")
    return _compute_source_chunk(
        task,
        _WORKER_FORWARD,
        _WORKER_INVERSE,
        _WORKER_ORIGIN_X,
        _WORKER_ORIGIN_Y,
    )


def _terminate_source_executor(executor: ProcessPoolExecutor) -> None:
    """Stop worker processes after a timeout or worker failure."""
    for process in (executor._processes or {}).values():
        if process.is_alive():
            process.terminate()
    executor.shutdown(wait=True, cancel_futures=True)


'''
assert marker in s
s=s.replace(marker,helpers+marker,1)
p.write_text(s)
