"""Backend microbenchmarks for batched bearing residual evaluation.

This module measures the embarrassingly parallel part of localization: computing
angular residuals for many candidate positions against AIS points.  It does *not*
implement full data association (for example, a Hungarian assignment), so its
timings must not be interpreted as end-to-end localization latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from time import perf_counter

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


def _wrap_deg(values: np.ndarray) -> np.ndarray:
    return (values + 180.0) % 360.0 - 180.0


def angular_cost_cpu(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
) -> np.ndarray:
    """Return absolute bearing residuals with shape ``(C, O, A)`` on CPU."""
    poses = np.asarray(candidate_xy, dtype=float)
    obs = np.asarray(observed_bearing_deg, dtype=float).reshape(-1)
    ais = np.asarray(ais_xy, dtype=float)
    absolute = np.rad2deg(
        np.arctan2(ais[None, :, 0] - poses[:, None, 0], ais[None, :, 1] - poses[:, None, 1])
    )
    return np.abs(_wrap_deg(absolute[:, None, :] - obs[None, :, None]))


def benchmark_backends(
    candidate_xy: np.ndarray,
    observed_bearing_deg: np.ndarray,
    ais_xy: np.ndarray,
    repeats: int = 3,
) -> list[BackendTiming]:
    """Benchmark NumPy and CuPy when installed, including GPU transfer time."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    poses = np.asarray(candidate_xy, dtype=np.float32)
    obs = np.asarray(observed_bearing_deg, dtype=np.float32)
    ais = np.asarray(ais_xy, dtype=np.float32)
    shape = (len(poses), len(obs), len(ais))

    cpu_times = []
    for _ in range(repeats):
        start = perf_counter()
        angular_cost_cpu(poses, obs, ais)
        cpu_times.append(perf_counter() - start)
    results = [
        BackendTiming(
            "numpy",
            True,
            *shape[:1],
            shape[1],
            shape[2],
            float(np.median(cpu_times)),
            0.0,
            float(np.median(cpu_times)),
        )
    ]

    try:
        cp = import_module("cupy")
    except ImportError as exc:
        results.append(
            BackendTiming("cupy", False, *shape[:1], shape[1], shape[2], None, None, None, str(exc))
        )
        return results

    try:
        kernel = []
        transfers = []
        e2e = []
        for _ in range(repeats):
            start = perf_counter()
            device_poses = cp.asarray(poses)
            device_obs, device_ais = cp.asarray(obs), cp.asarray(ais)
            cp.cuda.Stream.null.synchronize()
            transfer = perf_counter() - start
            transfers.append(transfer)
            start = perf_counter()
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
            kernel.append(perf_counter() - start)
            e2e.append(transfer + kernel[-1])
            del residual
        results.append(
            BackendTiming(
                "cupy",
                True,
                *shape[:1],
                shape[1],
                shape[2],
                float(np.median(kernel)),
                float(np.median(transfers)),
                float(np.median(e2e)),
            )
        )
    except Exception as exc:  # GPU unavailable or runtime failure
        results.append(
            BackendTiming(
                "cupy", False, *shape[:1], shape[1], shape[2], None, None, None, repr(exc)
            )
        )
    return results
