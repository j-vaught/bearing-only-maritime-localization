from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

from bearing_localization.search.assigned_bearing_refinement import AssignedBearingRefinementResult
from bearing_localization.search.likelihood import LikelihoodCostMatrix
from bearing_localization.search.prediction import PredictedGroupSet
from bearing_localization.search.scoring import CandidateEvaluation, CandidateScorer
from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from tools.run_assigned_refinement_diagnostic import (
    DiagnosticInputError,
    _hypothesis,
    _row_from_dict,
    canonical_lock_path,
    load_a20_bundle,
    run_start,
    verify_a20_package_pin,
)


def hypothesis(east: float = 1.0, north: float = 2.0, score: float = 3.0) -> Hypothesis:
    return Hypothesis(
        east,
        north,
        score,
        assignments=(Assignment(0, "catalog:one", residual_deg=0.1, cost=0.2),),
    )


def row(ordinal: int = 0) -> tuple[dict, dict]:
    cached = hypothesis(score=3.0)
    refined = hypothesis(east=1.5, north=2.5, score=2.0)
    selected = {
        "input_index": ordinal,
        "candidate_id": f"cell:cell:{ordinal}:0",
        "chunk_name": "chunk-00000000.json",
        "chunk_sha256": "a" * 64,
        "sidecar_sha256": "b" * 64,
        "cached_hypothesis": asdict(cached),
    }
    source = {
        **selected,
        "seed_order": ordinal,
        "refined_mode": {"hypothesis": asdict(refined)},
    }
    return source, selected


def make_bundle(root: Path, count: int = 64) -> None:
    selected = []
    rows = []
    for index in range(count):
        source, plan = row(index)
        selected.append(plan)
        rows.append(json.dumps(source, sort_keys=True, separators=(",", ":")))
    rows_path = root / "refined-modes.jsonl"
    rows_path.write_text("\n".join(rows) + "\n")
    manifest = {
        "schema": "cached-mode-refinement-v1",
        "status": "selected_modes_completed",
        "completed_mode_count": count,
        "selected_mode_count": count,
        "selected_modes": selected,
        "rows_file": rows_path.name,
        "rows_sha256": __import__("hashlib").sha256(rows_path.read_bytes()).hexdigest(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_complete_bundle_has_real_mode_rows(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    rows, manifest = load_a20_bundle(tmp_path)
    assert len(rows) == 64
    assert rows[0].cached_hypothesis.assignments[0].catalog_key == "catalog:one"
    assert manifest["selected_mode_count"] == 64


def test_partial_bundle_is_rejected(tmp_path: Path) -> None:
    make_bundle(tmp_path, count=50)
    with pytest.raises(DiagnosticInputError, match="64"):
        load_a20_bundle(tmp_path)


def test_invalid_rows_hash_is_rejected(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["rows_sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DiagnosticInputError, match="hash"):
        load_a20_bundle(tmp_path)


def test_rows_file_must_be_a_single_safe_basename(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["rows_file"] = "nested/rows.jsonl"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DiagnosticInputError, match="basename"):
        load_a20_bundle(tmp_path)


def test_canonical_lock_is_repository_benchmark_lock() -> None:
    lock = canonical_lock_path()
    assert lock.name == "benchmark.lock"
    assert lock.parent.name == ".agent-work"


def test_a20_package_commit_is_pinned_to_scoring_source() -> None:
    identity = {"scoring_source_sha": "source-commit"}
    verify_a20_package_pin({"package_commit": "source-commit"}, identity)
    with pytest.raises(DiagnosticInputError, match="package commit"):
        verify_a20_package_pin({"package_commit": "other-commit"}, identity)


def test_stale_source_row_and_status_boolean_are_rejected() -> None:
    source, selected = row()
    source["candidate_id"] = "cell:cell:stale"
    with pytest.raises(DiagnosticInputError, match="source reference"):
        _row_from_dict(source, selected, 0)
    source, selected = row()
    source["refined_mode"]["hypothesis"] = True
    with pytest.raises(DiagnosticInputError, match="Hypothesis mapping"):
        _row_from_dict(source, selected, 0)


def test_hypothesis_decoder_returns_contract_dataclass() -> None:
    decoded = _hypothesis(asdict(hypothesis()), "source")
    assert isinstance(decoded, Hypothesis)
    assert isinstance(decoded.assignments[0], Assignment)


class FakeScorer:
    def __init__(self, exact: Hypothesis):
        self.exact = exact

    def evaluate(self, _position: tuple[float, float]) -> CandidateEvaluation:
        return CandidateEvaluation(
            "scored",
            self.exact,
            cast(PredictedGroupSet, object()),
            cast(LikelihoodCostMatrix, object()),
        )


def test_solver_exception_is_retained_as_explicit_partial_step() -> None:
    source = hypothesis()

    def failed(_scorer, _hypothesis):
        raise RuntimeError("solver failed")

    result = run_start(
        cast(CandidateScorer, FakeScorer(source)), source, "cached_hypothesis", proposer=failed
    )
    assert result.stop_reason == "proposal_exception"
    assert result.steps[0]["status"] == "proposal_exception"
    assert result.final_hypothesis == source


def test_timeout_propagates_to_outer_interrupted_state() -> None:
    source = hypothesis()

    def interrupted(_scorer, _hypothesis):
        raise TimeoutError("deadline")

    with pytest.raises(TimeoutError, match="deadline"):
        run_start(
            cast(CandidateScorer, FakeScorer(source)),
            source,
            "cached_hypothesis",
            proposer=interrupted,
        )


def test_max_nfev_is_a_failure_stop_and_not_an_accepted_step() -> None:
    source = hypothesis()

    def maxed(_scorer, current):
        return AssignedBearingRefinementResult(
            original_hypothesis=current,
            proposed_pose=(current.east_m + 10.0, current.north_m),
            exact_hypothesis=None,
            status="max_nfev",
            solver_status=0,
            nfev=100,
            njev=50,
            residual_count=2,
            residual_evaluations=100,
            assignment_count=1,
            message="maximum evaluations",
        )

    result = run_start(
        cast(CandidateScorer, FakeScorer(source)), source, "refined_mode.hypothesis", proposer=maxed
    )
    assert result.stop_reason == "max_nfev"
    assert result.final_hypothesis == source


def test_stale_initial_score_is_rejected() -> None:
    source = hypothesis(score=99.0)
    canonical = hypothesis(score=3.0)
    with pytest.raises(DiagnosticInputError, match="score"):
        run_start(cast(CandidateScorer, FakeScorer(canonical)), source, "cached_hypothesis")


def test_ranking_schema_names_selected_mode_scope() -> None:
    source = Path(__file__).parents[2] / "tools" / "run_assigned_refinement_diagnostic.py"
    text = source.read_text()
    assert 'manifest["ranked_selected_modes"]' in text
    assert "ranked_global_best" not in text
