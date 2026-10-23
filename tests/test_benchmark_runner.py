"""Tests for the dependency-injected experiment benchmark runner."""

from __future__ import annotations

from pathlib import Path

from bearing_localization.evaluation.benchmark import (
    BenchmarkCase,
    BenchmarkConfig,
    BenchmarkSearchRequest,
    run_benchmark,
    write_benchmark,
)
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    EvaluationTruth,
    MeasurementSet,
    Observation,
    PositionEstimate,
    TrueObject,
)


def _number(row: dict[str, object], key: str) -> float:
    value = row[key]
    assert isinstance(value, int | float)
    return float(value)


def _case() -> BenchmarkCase:
    measurements = MeasurementSet(
        observations=(
            Observation(bearing_deg=0.0, bearing_sigma_deg=0.2, object_type="ship"),
            Observation(bearing_deg=90.0, bearing_sigma_deg=0.2, object_type="buoy"),
            Observation(bearing_deg=-90.0, bearing_sigma_deg=0.2, object_type="ship"),
        ),
        trial_id="trial-0",
    )
    ais = AISSnapshot(
        records=(
            AISRecord("ais-0", 0.0, 100.0, "ship"),
            AISRecord("ais-1", 100.0, 0.0, "buoy"),
            AISRecord("ais-2", -100.0, 0.0, "ship"),
        ),
        timestamp_utc="2023-07-15T12:00:00Z",
        crs="local-EN",
        snapshot_id="snapshot-0",
    )
    truth = EvaluationTruth(
        observer_east_m=0.0,
        observer_north_m=0.0,
        observer_heading_deg=0.0,
        objects=(
            TrueObject("truth-0", "ship", 0.0, 100.0, "ais-0", 0),
            TrueObject("truth-1", "buoy", 100.0, 0.0, "ais-1", 1),
            TrueObject("truth-2", "ship", -100.0, 0.0, "ais-2", 2),
        ),
        trial_id="trial-0",
    )
    return BenchmarkCase(measurements, ais, truth)


def test_runner_sweeps_ranges_and_type_labels_without_exposing_truth() -> None:
    observed_conditions: list[tuple[float, bool]] = []

    def prepare(seed: int) -> int:
        return seed

    def simulate(seed: int, range_m: float, use_types: bool, _trial_seed: int) -> BenchmarkCase:
        assert isinstance(seed, int)
        observed_conditions.append((range_m, use_types))
        return _case()

    def search(
        measurements: MeasurementSet,
        ais: AISSnapshot,
        request: BenchmarkSearchRequest,
    ) -> PositionEstimate:
        assert len(measurements.observations) == 3
        assert len(ais.records) == 3
        return PositionEstimate(
            east_m=0.0,
            north_m=0.0,
            score=0.0,
            elapsed_s=0.001,
            algorithm=f"fake-{request.use_types}-{request.visibility_radius_m}",
            matched_pairs=((0, "ais-0"), (1, "ais-1"), (2, "ais-2")),
        )

    result = run_benchmark(
        BenchmarkConfig(
            trials=2,
            seed=7,
            visibility_ranges_m=(1_000.0, 5_000.0),
            type_labels=(True, False),
        ),
        prepare_trial=prepare,
        simulate_trial=simulate,
        search_methods={"fake": search},
    )

    assert len(result.trials) == 8
    assert len(result.summary) == 4
    assert set(observed_conditions) == {
        (1_000.0, True),
        (1_000.0, False),
        (5_000.0, True),
        (5_000.0, False),
    }
    assert all(row["success"] for row in result.trials)
    assert all(row["correct_associations"] == 3 for row in result.trials)
    assert all(_number(row, "end_to_end_ms") >= _number(row, "search_ms") for row in result.trials)
    assert all(
        _number(row, "median_end_to_end_ms") >= _number(row, "median_search_ms")
        for row in result.summary
    )


def test_benchmark_writes_trial_and_summary_csv(tmp_path: Path) -> None:
    result = run_benchmark(
        BenchmarkConfig(trials=1, visibility_ranges_m=(1_000.0,), type_labels=(True,)),
        prepare_trial=lambda seed: seed,
        simulate_trial=lambda *_args: _case(),
        search_methods={
            "fake": lambda _measurements, _ais, _request: PositionEstimate(
                0.0,
                0.0,
                0.0,
                0.001,
                "fake",
                ((0, "ais-0"), (1, "ais-1"), (2, "ais-2")),
            )
        },
    )

    trial_path, summary_path = write_benchmark(result, tmp_path)

    assert trial_path.exists()
    assert summary_path.exists()
    assert "end_to_end_ms" in trial_path.read_text(encoding="utf-8").splitlines()[0]
    assert "p95_search_ms" in summary_path.read_text(encoding="utf-8").splitlines()[0]
