"""Optional CPU/CuPy bearing kernels and transfer-aware microbenchmarks.

The benchmark isolates batched bearing residual evaluation. It excludes data
association and should not be treated as full localization latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from time import perf_counter
from typing import Any

import numpy as np


@dataclass(frozen=True)
class BackendTiming:
    backend: str
    available: bool
    candidates: int
    observations: int
    ais_objects: int
    kernel_s: float | None
    transfer_s: float | None
    end_to_end_s: float | None
    error: str | None = None
    transfer_in_s: float | None = None
    transfer_out_s: float | None = None


def _validated_inputs(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
    *,
    dtype: np.dtype[Any] | type = np.float64,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    poses = np.asarray(candidate_xy, dtype=dtype)
    obs = np.asarray(observed_bearing_deg, dtype=dtype).reshape(-1)
    ais = np.asarray(ais_xy, dtype=dtype)
    if poses.ndim != 2 or poses.shape[1] != 2:
        raise ValueError("candidate_xy must have shape (C, 2)")
    if ais.ndim != 2 or ais.shape[1] != 2:
        raise ValueError("ais_xy must have shape (A, 2)")
    if not (np.isfinite(poses).all() and np.isfinite(obs).all() and np.isfinite(ais).all()):
        raise ValueError("bearing-kernel inputs must be finite")
    return poses, obs, ais


def _wrap_deg(values: np.ndarray) -> np.ndarray:
    return (values + 180.0) % 360.0 - 180.0


def angular_cost_cpu(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
) -> np.ndarray:
    """Return absolute global-bearing residuals with shape ``(C, O, A)``.

    Coordinates use ``(east, north)`` and bearings are clockwise from north.
    Empty candidate, observation, or AIS dimensions are supported.
    """
    poses, obs, ais = _validated_inputs(candidate_xy, observed_bearing_deg, ais_xy)
    absolute = np.rad2deg(
        np.arctan2(ais[None, :, 0] - poses[:, None, 0], ais[None, :, 1] - poses[:, None, 1])
    )
    return np.abs(_wrap_deg(absolute[:, None, :] - obs[None, :, None]))


def angular_cost_cupy(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
    *,
    cupy_module: Any | None = None,
) -> np.ndarray:
    """Compute the same residual tensor using CuPy and return it on the CPU.

    CuPy is imported lazily. Callers may inject a compatible module for testing
    or to reuse an already imported CuPy installation.
    """
    poses, obs, ais = _validated_inputs(
        candidate_xy, observed_bearing_deg, ais_xy, dtype=np.float32
    )
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    device_poses = cp.asarray(poses)
    device_obs = cp.asarray(obs)
    device_ais = cp.asarray(ais)
    absolute = cp.degrees(
        cp.arctan2(
            device_ais[None, :, 0] - device_poses[:, None, 0],
            device_ais[None, :, 1] - device_poses[:, None, 1],
        )
    )
    residual = cp.abs(
        (absolute[:, None, :] - device_obs[None, :, None] + 180.0) % 360.0 - 180.0
    )
    cp.cuda.Stream.null.synchronize()
    return cp.asnumpy(residual)


def _cupy_kernel(cp: Any, poses: Any, obs: Any, ais: Any) -> Any:
    absolute = cp.degrees(
        cp.arctan2(
            ais[None, :, 0] - poses[:, None, 0],
            ais[None, :, 1] - poses[:, None, 1],
        )
    )
    return cp.abs((absolute[:, None, :] - obs[None, :, None] + 180.0) % 360.0 - 180.0)


def benchmark_backends(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
    repeats: int = 3,
) -> list[BackendTiming]:
    """Benchmark NumPy and optional CuPy, including both host/device transfers.

    Timings are medians over ``repeats``. GPU timing synchronizes around each
    measured section and includes returning the residual tensor to host memory.
    One untimed GPU warmup is used to exclude first-use/context setup overhead.
    """
    if repeats < 1:
        raise ValueError("repeats must be positive")
    poses, obs, ais = _validated_inputs(
        candidate_xy, observed_bearing_deg, ais_xy, dtype=np.float32
    )
    shape = (len(poses), len(obs), len(ais))

    cpu_times = []
    for _ in range(repeats):
        start = perf_counter()
        angular_cost_cpu(poses, obs, ais)
        cpu_times.append(perf_counter() - start)
    cpu_median = float(np.median(cpu_times))
    results = [
        BackendTiming(
            backend="numpy",
            available=True,
            candidates=shape[0],
            observations=shape[1],
            ais_objects=shape[2],
            kernel_s=cpu_median,
            transfer_s=0.0,
            end_to_end_s=cpu_median,
            transfer_in_s=0.0,
            transfer_out_s=0.0,
        )
    ]

    try:
        cp = import_module("cupy")
    except ImportError as exc:
        results.append(
            BackendTiming(
                "cupy", False, *shape, None, None, None, str(exc), None, None
            )
        )
        return results

    try:
        # Warm the runtime and kernel before collecting stable measurements.
        warm_poses, warm_obs, warm_ais = cp.asarray(poses), cp.asarray(obs), cp.asarray(ais)
        warm_residual = _cupy_kernel(cp, warm_poses, warm_obs, warm_ais)
        cp.cuda.Stream.null.synchronize()
        cp.asnumpy(warm_residual)
        cp.cuda.Stream.null.synchronize()
        del warm_poses, warm_obs, warm_ais, warm_residual

        kernel_times: list[float] = []
        transfer_in_times: list[float] = []
        transfer_out_times: list[float] = []
        end_to_end_times: list[float] = []
        for _ in range(repeats):
            overall_start = perf_counter()
            transfer_start = perf_counter()
            device_poses = cp.asarray(poses)
            device_obs = cp.asarray(obs)
            device_ais = cp.asarray(ais)
            cp.cuda.Stream.null.synchronize()
            transfer_in_times.append(perf_counter() - transfer_start)

            kernel_start = perf_counter()
            residual = _cupy_kernel(cp, device_poses, device_obs, device_ais)
            cp.cuda.Stream.null.synchronize()
            kernel_times.append(perf_counter() - kernel_start)

            transfer_start = perf_counter()
            cp.asnumpy(residual)
            cp.cuda.Stream.null.synchronize()
            transfer_out_times.append(perf_counter() - transfer_start)
            end_to_end_times.append(perf_counter() - overall_start)
            del device_poses, device_obs, device_ais, residual

        transfer_in = float(np.median(transfer_in_times))
        transfer_out = float(np.median(transfer_out_times))
        results.append(
            BackendTiming(
                backend="cupy",
                available=True,
                candidates=shape[0],
                observations=shape[1],
                ais_objects=shape[2],
                kernel_s=float(np.median(kernel_times)),
                transfer_s=transfer_in + transfer_out,
                end_to_end_s=float(np.median(end_to_end_times)),
                transfer_in_s=transfer_in,
                transfer_out_s=transfer_out,
            )
        )
    except Exception as exc:  # GPU can be installed but unavailable at runtime.
        results.append(
            BackendTiming("cupy", False, *shape, None, None, None, repr(exc), None, None)
        )
    return results
