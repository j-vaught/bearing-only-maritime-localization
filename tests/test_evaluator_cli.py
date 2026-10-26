"""Integration tests for the standalone truth-consuming evaluator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maritime.evaluate_cli import main
from maritime.schemas import EvaluationTruth, PositionEstimate
from maritime.serialization import write_contract


def test_evaluator_cli_reads_estimate_and_separate_truth(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estimate_path = tmp_path / "estimate.json"
    truth_path = tmp_path / "truth.json"
    metrics_path = tmp_path / "metrics.json"
    write_contract(
        estimate_path,
        PositionEstimate(
            east_m=10.0,
            north_m=20.0,
            score=0.0,
            elapsed_s=0.001,
            algorithm="test",
        ),
    )
    write_contract(
        truth_path,
        EvaluationTruth(
            observer_east_m=10.0,
            observer_north_m=20.0,
            observer_heading_deg=0.0,
            objects=(),
            trial_id="trial-1",
        ),
    )

    assert (
        main(
            [
                "--estimate",
                str(estimate_path),
                "--truth",
                str(truth_path),
                "--output",
                str(metrics_path),
            ]
        )
        == 0
    )

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["position_error_m"] == 0.0
    assert metrics["correct_associations"] == 0
    assert not metrics["success"]
    assert json.loads(capsys.readouterr().out)["position_error_m"] == 0.0


def test_evaluator_rejects_truth_and_estimate_in_same_file(tmp_path: Path) -> None:
    shared_path = tmp_path / "same.json"
    write_contract(
        shared_path,
        PositionEstimate(
            east_m=0.0,
            north_m=0.0,
            score=0.0,
            elapsed_s=0.001,
            algorithm="test",
        ),
    )

    with pytest.raises(ValueError, match="separate contract files"):
        from maritime.evaluate_cli import evaluate_files

        evaluate_files(shared_path, shared_path)
