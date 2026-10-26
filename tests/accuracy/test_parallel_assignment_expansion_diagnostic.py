# ruff: noqa: E402
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from tools import run_parallel_assignment_expansion_diagnostic as runner


def hypothesis(score: float, east: float) -> Hypothesis:
    return Hypothesis(
        east,
        2.0,
        score,
        assignments=(Assignment(0, "catalog:one", residual_deg=0.1, cost=0.2),),
    )


def assigned_row(index: int) -> dict:
    return {
        "mode_index": index,
        "input_index": index + 10,
        "candidate_id": f"cell:{index}",
        "starts": [
            {"label": "cached_hypothesis", "final_hypothesis": asdict(hypothesis(2.0, 1.0))},
            {
                "label": "refined_mode.hypothesis",
                "final_hypothesis": asdict(hypothesis(1.0, 3.0)),
            },
        ],
    }


def test_standalone_import_sets_numeric_thread_limits_before_dependencies() -> None:
    code = (
        "import os; import tools.run_parallel_assignment_expansion_diagnostic; "
        "print(','.join(os.environ[name] for name in "
        "('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env={key: value for key, value in os.environ.items() if not key.endswith("_NUM_THREADS")},
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == "1,1,1,1"


def test_input_selection_matches_serial_best_of_two_and_keeps_mode_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True)
    class FakeModeTask:
        mode_index: int
        seed: Hypothesis
        config: object
        package_commit: str
        input_digest: str
        source_digest: str
        candidate_branch_count: int

    fake_pool = types.ModuleType("bearing_localization.search.parallel_assignment_expansion")
    setattr(fake_pool, "ModeTask", FakeModeTask)
    monkeypatch.setitem(
        sys.modules, "bearing_localization.search.parallel_assignment_expansion", fake_pool
    )
    monkeypatch.setattr(
        "bearing_localization.search.assignment_expansion._candidate_pool",
        lambda _scorer, _seed, _config: (
            ["eligible"],
            {"eligible_records": 4, "unmatched_reports": 3},
            "pool-digest",
        ),
    )
    rows = [assigned_row(0), assigned_row(1)]
    scorer = SimpleNamespace(
        evaluate=lambda position: SimpleNamespace(
            status="scored",
            hypothesis=next(
                item
                for row in rows
                for start in row["starts"]
                if (item := runner.serial._hypothesis(start["final_hypothesis"], "seed")).east_m
                == position[0]
            ),
        )
    )
    config = object()
    pins = {"source": "frozen"}
    identity = {"package_commit": "abc123"}
    tasks, metadata = runner.prepare_mode_tasks(rows, pins, identity, config, scorer)
    assert [task.mode_index for task in tasks] == [0, 1]
    assert [task.seed.east_m for task in tasks] == [3.0, 3.0]
    assert [task.candidate_branch_count for task in tasks] == [12, 12]
    assert len({task.source_digest for task in tasks}) == 1
    assert [item["selected_start_label"] for item in metadata.values()] == [
        "refined_mode.hypothesis",
        "refined_mode.hypothesis",
    ]
    assert [item["candidate_id"] for item in metadata.values()] == ["cell:0", "cell:1"]
    assert len({task.input_digest for task in tasks}) == 2
    assert all(len(item["start_alternatives"]) == 2 for item in metadata.values())
    assert all(item["seed_stage"] == "best_saved" for item in metadata.values())
    assert all(
        item["candidate_branch_count_delta_vs_best_saved"] == 0 for item in metadata.values()
    )
    explicit_tasks, explicit_metadata = runner.prepare_mode_tasks(
        rows, pins, identity, config, scorer, seed_stage="best_saved"
    )
    assert explicit_tasks == tasks
    assert explicit_metadata == metadata


def test_coarse_seed_uses_original_a20_cached_pose_despite_better_saved_refined_pose() -> None:
    from bearing_localization.search.assigned_bearing_refinement import refine_assigned_bearing
    from bearing_localization.search.assignment_expansion import ExpansionConfig
    from bearing_localization.search.config import SearchConfig
    from bearing_localization.search.refinement import refine_modes
    from bearing_localization.search.scoring import CandidateScorer
    from tests.accuracy.test_a20 import _inputs, _xy

    measurements, catalog, vector_map = _inputs(noisy=True)
    east, north = _xy()
    search_config = SearchConfig(
        bounds_xy=(east - 1000.0, east + 1000.0, north - 1000.0, north + 1000.0),
        max_range_m=8000.0,
    )
    scorer = CandidateScorer(measurements, catalog, vector_map, search_config)
    original_cached = scorer.evaluate((east + 250.0, north - 250.0)).hypothesis
    assert original_cached is not None
    a20_refinement = refine_modes(
        measurements,
        catalog,
        vector_map,
        search_config,
        [original_cached],
        initial_step_m=250,
    ).modes[0]
    pattern_refined = a20_refinement.hypothesis
    assert pattern_refined is not None and pattern_refined.score < original_cached.score
    assigned_refinement = refine_assigned_bearing(scorer, pattern_refined)
    assigned_refined = assigned_refinement.exact_hypothesis or pattern_refined
    assert assigned_refined.score < original_cached.score

    mode_identity = {"candidate_id": "production-fixture-cell", "input_index": 17}
    a20_row = {
        **mode_identity,
        "cached_hypothesis": asdict(original_cached),
        "refined_mode": {"hypothesis": asdict(pattern_refined)},
    }
    assigned_row = {
        "mode_index": 0,
        **mode_identity,
        "starts": [
            {"label": "cached_hypothesis", "final_hypothesis": asdict(pattern_refined)},
            {"label": "refined_mode.hypothesis", "final_hypothesis": asdict(assigned_refined)},
        ],
    }
    identity = runner._loaded_package_identity()
    config = ExpansionConfig(local_radius_m=1000.0, max_nfev=100, max_branches=5000)
    pins = {"fixture_source": "production CandidateScorer with noisy A20 inputs"}
    coarse_tasks, coarse_rows = runner.prepare_mode_tasks(
        [assigned_row],
        pins,
        identity,
        config,
        scorer,
        seed_stage="coarse",
        a20_rows=[a20_row],
    )
    default_tasks, default_rows = runner.prepare_mode_tasks(
        [assigned_row], pins, identity, config, scorer
    )

    assert coarse_tasks[0].seed.score == original_cached.score
    assert coarse_rows[0]["selected_start_label"] == "a20.cached_hypothesis"
    assert coarse_rows[0]["candidate_id"] == mode_identity["candidate_id"]
    assert default_tasks[0].seed.score == assigned_refined.score
    expected_best_label, _ = runner.serial.choose_best_start(assigned_row)
    assert default_rows[0]["selected_start_label"] == expected_best_label
    assert default_tasks[0].seed.score < original_cached.score
    assert coarse_tasks[0].seed.score > default_tasks[0].seed.score
    assert coarse_tasks[0].source_digest != default_tasks[0].source_digest
    assert coarse_tasks[0].input_digest != default_tasks[0].input_digest
    assert coarse_rows[0]["candidate_pool_counts"]["eligible_records"] >= 0
    assert coarse_rows[0]["candidate_branch_count_delta_vs_best_saved"] == (
        coarse_rows[0]["candidate_branch_count"]
        - coarse_rows[0]["best_saved_candidate_pool_counts"]["eligible_records"]
        * coarse_rows[0]["best_saved_candidate_pool_counts"]["unmatched_reports"]
    )
    assert sum(item["selected"] for item in coarse_rows[0]["start_alternatives"]) == 1
    assert all(item["canonical_match"] for item in coarse_rows[0]["start_alternatives"])
    assert all(item["canonical_match"] for item in default_rows[0]["start_alternatives"])

    with pytest.raises(runner.serial.DiagnosticInputError, match="A20 origin"):
        runner.prepare_mode_tasks(
            [assigned_row],
            pins,
            identity,
            config,
            scorer,
            seed_stage="coarse",
            a20_rows=[{**a20_row, "candidate_id": "mismatched-cell"}],
        )


def test_seed_stage_must_be_explicitly_supported() -> None:
    with pytest.raises(ValueError, match="seed_stage"):
        runner.prepare_mode_tasks([], {}, {}, object(), object(), seed_stage="truth-selected")


def test_worker_source_hash_mismatch_is_rejected() -> None:
    expansion_source = str(
        runner._REPO_ROOT / "bearing_localization/search/assignment_expansion.py"
    )
    expected = {
        "package_commit": "commit",
        "assignment_expansion_sha256": "a" * 64,
        "assignment_expansion_source": expansion_source,
    }
    scorer_source = runner._REPO_ROOT / "bearing_localization/search/scoring.py"
    workers = [
        {
            "worker_id": index,
            **expected,
            "scorer_source": str(scorer_source),
            "scorer_sha256": runner._sha256(scorer_source),
        }
        for index in range(4)
    ]
    workers[2]["assignment_expansion_sha256"] = "0" * 64
    with pytest.raises(runner.serial.DiagnosticInputError, match="source differs"):
        runner._validate_worker_provenance(workers, expected)


def test_worker_count_and_loaded_path_hashes_are_required() -> None:
    expected = {
        "package_commit": "commit",
        "assignment_expansion_sha256": "a" * 64,
        "assignment_expansion_source": str(
            runner._REPO_ROOT / "bearing_localization/search/assignment_expansion.py"
        ),
    }
    with pytest.raises(runner.serial.DiagnosticInputError, match="all four"):
        runner._validate_worker_provenance([], expected)


def test_dispatch_priority_is_count_descending_with_stable_mode_ties() -> None:
    tasks = [
        SimpleNamespace(mode_index=4, candidate_branch_count=10),
        SimpleNamespace(mode_index=1, candidate_branch_count=20),
        SimpleNamespace(mode_index=2, candidate_branch_count=20),
        SimpleNamespace(mode_index=0, candidate_branch_count=3),
    ]
    ordered = runner._dispatch_order(tasks)
    assert [(item.mode_index, item.candidate_branch_count) for item in ordered] == [
        (1, 20),
        (2, 20),
        (4, 10),
        (0, 3),
    ]


def test_branch_inventory_keeps_candidate_visited_and_unvisited_totals() -> None:
    rows = [
        {
            "counters": {
                "candidate_branches": 12,
                "visited_branches": 10,
                "unvisited_branches": 2,
            }
        },
        {
            "counters": {
                "candidate_branches": 8,
                "visited_branches": 8,
                "unvisited_branches": 0,
            }
        },
    ]
    assert runner._branch_inventory(rows) == {
        "candidate_branches": 20,
        "visited_branches": 18,
        "unvisited_branches": 2,
    }


def test_runner_has_no_truth_or_evaluator_imports() -> None:
    tree = ast.parse(
        (_REPO_ROOT / "tools/run_parallel_assignment_expansion_diagnostic.py").read_text()
    )
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    imported += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert all("truth" not in name.lower() and "evaluator" not in name.lower() for name in imported)


def test_interleaved_events_keep_global_chain_and_mode_local_indices(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    state = runner.DurableState()
    with events_path.open("w") as stream:
        runner.append_event(stream, state, 2, "branch_started", {"branch_id": 0})
        runner.append_event(stream, state, 0, "branch_started", {"branch_id": 0})
        runner.append_event(stream, state, 2, "branch_result", {"branch_id": 0})
    records = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert [(item["mode_index"], item["mode_event_index"]) for item in records] == [
        (2, 0),
        (0, 0),
        (2, 1),
    ]
    assert records[1]["previous_event_sha256"] == records[0]["event_sha256"]
    assert records[2]["previous_event_sha256"] == records[1]["event_sha256"]


def test_partial_durable_state_sorts_rows_and_tracks_unknown_modes(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    rows_path = output / "assignment-expansion-rows.jsonl"
    events_path = output / "branch-events.jsonl"
    rows_path.touch()
    events_path.touch()
    state = runner.DurableState(
        outcomes={2: {"mode_index": 2}, 0: {"mode_index": 0}},
        failures={5: {"status": "worker_failed"}},
    )
    manifest = {"schema": "partial"}
    runner._persist_state(output, manifest, state, "interrupted", "timeout")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    checkpoint = json.loads((output / "checkpoint.json").read_text())
    assert [row["mode_index"] for row in rows] == [0, 2]
    assert checkpoint["completed_mode_indices"] == [0, 2]
    assert checkpoint["failed_mode_indices"] == [5]
    assert checkpoint["unknown_mode_count"] == runner.MODE_COUNT - 3
    assert checkpoint["status"] == "interrupted"
    assert checkpoint["resume_supported"] is False


def test_canonical_root_lock_matches_serial_runner() -> None:
    lock = runner.serial.canonical_lock_path()
    assert lock.name == "benchmark.lock"
    assert lock.parent.name == ".agent-work"


def test_current_package_identity_uses_checkout_source_and_hash() -> None:
    identity = runner._loaded_package_identity()
    assert (
        identity["package_commit"]
        == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True).strip()
    )
    source = Path(identity["assignment_expansion_source"])
    assert source.is_relative_to(_REPO_ROOT)
    assert len(identity["assignment_expansion_sha256"]) == 64
    assert identity["assignment_expansion_sha256"] == runner._sha256(source)


def test_manifest_never_certifies_heuristic_search() -> None:
    assert runner.OUTPUT_NAME == "parallel-assignment-expansion-diagnostic-v1"
    assert runner.COARSE_OUTPUT_NAME == "parallel-coarse-assignment-expansion-diagnostic-v1"
    assert runner.WORKER_COUNT == 4
    assert runner.MAX_OUTPUT_BYTES == 50 * 1024**2
