from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.evaluate_a20_cached_refinement import evaluate_rows, load_refinement  # noqa: E402
from tools.evaluate_cached_finite_domain import LoadedRun, SavedCandidate, decode_hypothesis  # noqa: E402


def _hypothesis(east: float, score: float, key: str) -> dict[str, Any]:
    return {
        "east_m": east,
        "north_m": 4900000.0,
        "score": score,
        "assignments": [{"report_index": 0, "catalog_key": key, "residual_deg": 0.1, "cost": 1.0}],
        "score_components": {
            "matched_cost": score - 1.0,
            "clutter_cost": 1.0,
            "missed_cost": 0.0,
            "total_cost": score,
        },
    }


def _run() -> LoadedRun:
    hyp = decode_hypothesis(_hypothesis(500000.0, 2.0, "a"))
    return LoadedRun(
        {}, {}, (SavedCandidate(7, "candidate-7", "exact_scored", hyp),), {}, {}, (), (), False
    )


def test_evaluator_records_source_revalidated_and_refined_metrics() -> None:
    run = _run()
    row = {
        "seed_order": 0,
        "input_index": 7,
        "candidate_id": "candidate-7",
        "cached_hypothesis": _hypothesis(500000.0, 2.0, "a"),
        "revalidated_hypothesis": _hypothesis(500000.0, 2.0, "a"),
        "refined_mode": {
            "hypothesis": _hypothesis(500010.0, 1.5, "a"),
            "improved_hypothesis": True,
            "iterations": 4,
            "stop_reason": "score_improvement_tolerance",
            "history": [
                {"iteration": 0, "score": 2.0},
                {"iteration": 1, "score": 1.5},
            ],
            "uncertainty_rank": 2,
            "uncertainty_singular": False,
            "uncertainty_reason": None,
            "estimated_95_radius_m": 12.0,
        },
    }
    truth_xy = Transformer.from_crs("EPSG:32618", "EPSG:32619", always_xy=True).transform(
        500000.0, 4900000.0
    )
    result = evaluate_rows([row], run, truth_xy=truth_xy, truth_ids={0: {"a"}})[0]
    assert result["source"]["geodesic_error_m"] == pytest.approx(0.0, abs=1e-6)
    assert result["refined"]["geodesic_error_m"] == pytest.approx(10.0, abs=0.02)
    assert result["source"]["direct_truth_key_assignment_count"] == 1
    assert result["refined"]["direct_truth_key_assignment_count"] == 1
    assert result["source"]["score"] == 2.0
    assert result["revalidated"]["score"] == 2.0
    assert result["refined"]["score"] == 1.5
    assert result["mode_uncertainty"]["uncertainty_rank"] == 2
    assert result["improved_hypothesis"] is True
    assert result["seed_order"] == 0
    assert result["source_score_rank"] == 1
    assert result["refined_score_rank_within_selected"] == 1


def test_evaluator_keeps_null_refined_hypothesis_unavailable() -> None:
    row = {
        "seed_order": 0,
        "input_index": 7,
        "candidate_id": "candidate-7",
        "cached_hypothesis": _hypothesis(500000.0, 2.0, "a"),
        "revalidated_hypothesis": None,
        "refined_mode": {
            "hypothesis": None,
            "improved_hypothesis": False,
            "iterations": 0,
            "stop_reason": "unsupported_seed",
            "history": [{"iteration": 0, "score": 2.0}],
        },
    }
    truth_xy = Transformer.from_crs("EPSG:32618", "EPSG:32619", always_xy=True).transform(
        500000.0, 4900000.0
    )
    result = evaluate_rows([row], _run(), truth_xy=truth_xy, truth_ids={0: {"a"}})[0]
    assert result["source"] is not None
    assert result["revalidated"] is None
    assert result["refined"] is None


def test_refinement_loader_rejects_unknown_schema(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"schema":"unknown"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        load_refinement(tmp_path, tmp_path, _run())
