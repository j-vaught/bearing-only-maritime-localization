"""Small end-to-end smoke test for the public benchmark command."""

from __future__ import annotations

import csv
from pathlib import Path

from bearing_localization.benchmark_cli import main


def test_benchmark_cli_writes_trials_and_summary(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "benchmark"
    assert (
        main(
            [
                "--trials",
                "1",
                "--seed",
                "17",
                "--scene-km",
                "4",
                "--object-count",
                "24",
                "--ais-fraction",
                "0.75",
                "--visibility-ranges-km",
                "1",
                "--noise-deg",
                "0.1",
                "--angular-resolution-deg",
                "0.1",
                "--grid-spacing-m",
                "2000",
                "--algorithms",
                "grid",
                "--no-refine",
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    trial_path = output_dir / "benchmark_trials.csv"
    summary_path = output_dir / "benchmark_summary.csv"
    assert trial_path.is_file()
    assert summary_path.is_file()
    with trial_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2  # labeled and unlabeled conditions
    assert {row["type_labels"] for row in rows} == {"True", "False"}
    assert {row["visibility_range_m"] for row in rows} == {"1000.0"}
    assert "truth" not in rows[0]
    assert "end_to_end_ms" in rows[0]
    assert "p95_search_ms" in summary_path.read_text(encoding="utf-8").splitlines()[0]
    assert str(trial_path) in capsys.readouterr().out
