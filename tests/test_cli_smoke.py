"""Subprocess smoke test for the simulator-to-search contract boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_module(module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{existing_path}" if existing_path else str(PROJECT_ROOT)
    )
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_simulator_output_runs_search_without_truth_file(tmp_path: Path) -> None:
    measurements = tmp_path / "measurements.json"
    ais = tmp_path / "ais.json"
    truth = tmp_path / "truth.json"
    map_input = tmp_path / "map.json"
    estimate = tmp_path / "estimate.json"

    map_input.write_text(
        json.dumps(
            {
                "contract_type": "MapInput",
                "crs": "local-EN",
                "west_m": -3_000.0,
                "south_m": -3_000.0,
                "cell_size_m": 1_000.0,
                "water_mask": [[True] * 6 for _ in range(6)],
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    _run_module(
        "maritime.simulator.cli",
        "--seed",
        "42",
        "--scene-km",
        "6",
        "--object-count",
        "40",
        "--ais-fraction",
        "0.8",
        "--range-km",
        "5",
        "--observer-east-m",
        "0",
        "--observer-north-m",
        "0",
        "--observer-heading-deg",
        "0",
        "--map-input",
        str(map_input),
        "--measurements-out",
        str(measurements),
        "--ais-out",
        str(ais),
        "--truth-out",
        str(truth),
    )

    measurement_document = json.loads(measurements.read_text(encoding="utf-8"))
    ais_document = json.loads(ais.read_text(encoding="utf-8"))
    assert measurement_document["contract_type"] == "MeasurementSet"
    assert ais_document["contract_type"] == "AISSnapshot"
    assert measurement_document["observations"]
    assert truth.exists()

    # Search must succeed with only estimator inputs available on disk.
    truth.unlink()
    search_arguments = (
        "--measurements",
        str(measurements),
        "--ais",
        str(ais),
        "--map",
        str(map_input),
        "--bounds",
        "-3000,3000,-3000,3000",
        "--algorithm",
        "grid",
        "--grid-spacing-m",
        "2000",
        "--visibility-radius-m",
        "5000",
        "--no-refine",
        "--output",
        str(estimate),
    )
    assert str(truth) not in search_arguments
    _run_module("maritime.search_cli", *search_arguments)

    estimate_document = json.loads(estimate.read_text(encoding="utf-8"))
    assert estimate_document["contract_type"] == "PositionEstimate"
    assert estimate_document["algorithm"] == "grid"
    assert "observer_east_m" not in estimate_document
    assert "observer_north_m" not in estimate_document
