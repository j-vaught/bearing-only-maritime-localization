from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Standalone focused runs must resolve repository tooling without another test's imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.run_a26_screened_diagnostic import (  # noqa: E402
    CATALOG_SHA256,
    MAP_SHA256,
    MEASUREMENTS_SHA256,
    RUN_ID,
    SCOPE_SHA256,
    benchmark_lock_path,
    build_cache_generation_plan,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_frozen_cache_build_plan_uses_only_search_inputs(tmp_path: Path) -> None:
    repo_root = _repo_root()
    plan = build_cache_generation_plan(
        repo_root,
        repo_root,
        tmp_path / "a26-screened-cache-diagnostic",
    )
    assert plan["run_id"] == RUN_ID
    assert plan["purpose"] == "development cache-generation diagnostic"
    assert plan["search_truth_input"] is None
    assert plan["stage"] == "cache_generation_only"
    assert plan["timeout_s"] == 1800.0
    assert plan["input_sha256"] == {
        "measurements": MEASUREMENTS_SHA256,
        "scope_manifest": SCOPE_SHA256,
        "catalog": CATALOG_SHA256,
        "map": MAP_SHA256,
    }
    assert plan["budgets"]["exact_scoring_candidates"] == 1
    assert plan["budgets"]["refinement"] == "disabled"
    assert plan["budgets"]["source_pairs"] == 333340
    assert plan["budgets"]["candidate_cells"] == 5_000_000
    assert not any("truth" in argument.lower() for argument in plan["argv"])
    assert not any("evaluator" in argument.lower() for argument in plan["argv"])
    assert "--no-refine" in plan["argv"]
    config_path = Path(plan["argv"][plan["argv"].index("--config") + 1])
    config = json.loads(config_path.read_text())
    assert config["candidate_budget"] == 1
    assert Path(config["proposal_cache_path"]) == Path(plan["cache_path"])
    assert Path(plan["cache_path"]).parent.is_dir()
    assert config["max_range_m"] == 10000.0
    assert config["use_types"] is False


def test_prepare_refuses_changed_frozen_config_or_existing_cache(tmp_path: Path) -> None:
    repo_root = _repo_root()
    output_root = tmp_path / "a26-screened-cache-diagnostic"
    plan = build_cache_generation_plan(repo_root, repo_root, output_root)
    config_path = Path(plan["argv"][plan["argv"].index("--config") + 1])
    config_path.write_text("{}\n")
    with pytest.raises(FileExistsError, match="Refusing to overwrite frozen plan"):
        build_cache_generation_plan(repo_root, repo_root, output_root)


def test_prepare_rejects_a_preexisting_cache_directory(tmp_path: Path) -> None:
    repo_root = _repo_root()
    output_root = tmp_path / "a26-screened-cache-diagnostic"
    cache = output_root / "cache/proposals-v1"
    cache.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="Fresh cache destination"):
        build_cache_generation_plan(repo_root, repo_root, output_root)


def test_benchmark_lock_is_shared_at_project_root(tmp_path: Path) -> None:
    worktree = tmp_path / Path(".agent-work/worktrees/fixed-search")
    worktree.mkdir(parents=True)
    assert benchmark_lock_path(worktree) == tmp_path / Path(".agent-work/benchmark.lock")
