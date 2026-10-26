"""Injected, reproducible benchmark harness for simulator and search programs.

The harness depends only on shared input/output contracts. Callers inject
scene preparation, simulation, and search functions, keeping the benchmark
from importing simulator internals or passing truth into search.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import numpy as np

from maritime.schemas import (
    AISSnapshot,
    EvaluationTruth,
    MeasurementSet,
    PositionEstimate,
)

from .metrics import evaluate_estimate as score_estimate


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """Estimator inputs and evaluator-only truth returned by a simulator."""

    measurements: MeasurementSet
    ais: AISSnapshot
    truth: EvaluationTruth


@dataclass(frozen=True, slots=True)
class BenchmarkSearchRequest:
    """Search settings for one ablation condition."""

    visibility_radius_m: float
    use_types: bool
    seed: int


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Reproducible range and object-type ablation settings."""

    trials: int = 5
    seed: int = 20261023
    visibility_ranges_m: tuple[float, ...] = (1_000.0, 5_000.0, 10_000.0)
    type_labels: tuple[bool, ...] = (True, False)
    success_position_threshold_m: float = 250.0
    minimum_correct_associations: int = 3

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError("trials must be positive.")
        if not self.visibility_ranges_m or any(
            not np.isfinite(value) or value <= 0 for value in self.visibility_ranges_m
        ):
            raise ValueError("visibility_ranges_m must contain finite positive distances.")
        if len(set(self.visibility_ranges_m)) != len(self.visibility_ranges_m):
            raise ValueError("visibility_ranges_m must not contain duplicates.")
        if not self.type_labels:
            raise ValueError("type_labels must contain at least one ablation setting.")
        if len(set(self.type_labels)) != len(self.type_labels):
            raise ValueError("type_labels must not contain duplicates.")
        if self.success_position_threshold_m <= 0 or not np.isfinite(
            self.success_position_threshold_m
        ):
            raise ValueError("success_position_threshold_m must be finite and positive.")
        if self.minimum_correct_associations < 0:
            raise ValueError("minimum_correct_associations must be nonnegative.")


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Raw trial records and condition-level timing/accuracy summaries."""

    trials: tuple[dict[str, object], ...]
    summary: tuple[dict[str, object], ...]


PrepareTrial = Callable[[int], Any]
SimulateTrial = Callable[[Any, float, bool, int], BenchmarkCase]
SearchMethod = Callable[[MeasurementSet, AISSnapshot, BenchmarkSearchRequest], PositionEstimate]


def run_benchmark(
    config: BenchmarkConfig,
    *,
    prepare_trial: PrepareTrial,
    simulate_trial: SimulateTrial,
    search_methods: Mapping[str, SearchMethod],
) -> BenchmarkResult:
    """Run seeded range/type ablations and measure each processing stage.

    ``prepare_trial`` is called once per seed. ``simulate_trial`` receives the
    prepared scene, visibility range, type-label setting, and seed. Each search
    method receives only measurements, AIS, and an explicit request; hidden
    truth is reserved for the evaluator in this module.
    """
    if not search_methods:
        raise ValueError("At least one search method is required.")
    if any(not name.strip() for name in search_methods):
        raise ValueError("Search method names must be nonempty.")

    rng = np.random.default_rng(config.seed)
    trial_seeds = rng.integers(0, np.iinfo(np.uint32).max, size=config.trials, dtype=np.uint32)
    trial_rows: list[dict[str, object]] = []

    for trial_index, raw_seed in enumerate(trial_seeds):
        seed = int(raw_seed)
        preparation_started = perf_counter()
        prepared = prepare_trial(seed)
        preparation_s = perf_counter() - preparation_started

        for visibility_m in config.visibility_ranges_m:
            for use_types in config.type_labels:
                simulation_started = perf_counter()
                case = simulate_trial(prepared, visibility_m, use_types, seed)
                simulation_s = perf_counter() - simulation_started
                if not isinstance(case, BenchmarkCase):
                    raise TypeError("simulate_trial must return a BenchmarkCase.")

                request = BenchmarkSearchRequest(
                    visibility_radius_m=visibility_m,
                    use_types=use_types,
                    seed=seed,
                )
                for method_name, search_method in search_methods.items():
                    search_started = perf_counter()
                    estimate = search_method(case.measurements, case.ais, request)
                    search_s = perf_counter() - search_started
                    if not isinstance(estimate, PositionEstimate):
                        raise TypeError(
                            f"Search method {method_name!r} must return PositionEstimate."
                        )

                    evaluation_started = perf_counter()
                    metrics = evaluate_estimate(
                        estimate,
                        case.truth,
                        config.success_position_threshold_m,
                        config.minimum_correct_associations,
                    )
                    evaluation_s = perf_counter() - evaluation_started
                    end_to_end_s = preparation_s + simulation_s + search_s + evaluation_s
                    trial_rows.append(
                        {
                            "trial": trial_index,
                            "seed": seed,
                            "visibility_range_m": visibility_m,
                            "type_labels": use_types,
                            "algorithm": method_name,
                            "observed_features": len(case.measurements.observations),
                            "ais_records": len(case.ais.records),
                            "preparation_ms": preparation_s * 1_000.0,
                            "simulation_ms": simulation_s * 1_000.0,
                            "search_ms": search_s * 1_000.0,
                            "reported_search_ms": estimate.elapsed_s * 1_000.0,
                            "evaluation_ms": evaluation_s * 1_000.0,
                            "end_to_end_ms": end_to_end_s * 1_000.0,
                            **metrics,
                        }
                    )

    return BenchmarkResult(
        trials=tuple(trial_rows),
        summary=tuple(summarize_trials(trial_rows)),
    )


def evaluate_estimate(
    estimate: PositionEstimate,
    truth: EvaluationTruth,
    success_position_threshold_m: float = 250.0,
    minimum_correct_associations: int = 3,
) -> dict[str, object]:
    """Compute position and matched-association metrics using truth only here."""
    return asdict(
        score_estimate(
            estimate,
            truth,
            success_position_threshold_m=success_position_threshold_m,
            success_min_correct_associations=minimum_correct_associations,
        )
    )


def summarize_trials(trials: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Aggregate accuracy and stage/end-to-end latency by ablation condition."""
    grouped: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in trials:
        grouped[(row["visibility_range_m"], row["type_labels"], row["algorithm"])].append(row)

    timing_keys = (
        "preparation_ms",
        "simulation_ms",
        "search_ms",
        "reported_search_ms",
        "evaluation_ms",
        "end_to_end_ms",
    )
    summaries: list[dict[str, object]] = []
    for (visibility_m, type_labels, algorithm), rows in sorted(
        grouped.items(), key=lambda item: str(item[0])
    ):
        summary: dict[str, object] = {
            "visibility_range_m": visibility_m,
            "type_labels": type_labels,
            "algorithm": algorithm,
            "trials": len(rows),
            "median_observed_features": median(_as_float(row["observed_features"]) for row in rows),
            "success_rate": float(np.mean([bool(row["success"]) for row in rows])),
            "median_position_error_m": median(_as_float(row["position_error_m"]) for row in rows),
        }
        for key in timing_keys:
            values = np.asarray([_as_float(row[key]) for row in rows], dtype=float)
            summary[f"median_{key}"] = float(np.median(values))
            summary[f"p95_{key}"] = float(np.percentile(values, 95))
        summaries.append(summary)
    return summaries


def write_benchmark(result: BenchmarkResult, output_dir: str | Path) -> tuple[Path, Path]:
    """Write raw trial and aggregated summary CSV files."""
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    trial_path = destination / "benchmark_trials.csv"
    summary_path = destination / "benchmark_summary.csv"
    _write_csv(trial_path, result.trials)
    _write_csv(summary_path, result.summary)
    return trial_path, summary_path


def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write an empty benchmark table to {path}.")
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _as_float(value: object) -> float:
    if isinstance(value, bool | int | float):
        return float(value)
    raise TypeError(f"Expected a numeric benchmark value, received {value!r}.")


__all__ = [
    "BenchmarkCase",
    "BenchmarkConfig",
    "BenchmarkResult",
    "BenchmarkSearchRequest",
    "evaluate_estimate",
    "run_benchmark",
    "summarize_trials",
    "write_benchmark",
]
