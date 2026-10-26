from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.run_a26_cached_finite_domain import (
    append_jsonl,
    resolve_project_root,
    sha256_file,
    smoke_scoring_source,
    terminate_process_group,
    worker_exit_code,
    write_candidate_dispositions,
)


def test_chunk_checkpoint_appends_ordered_audit_rows(tmp_path: Path) -> None:
    path = tmp_path / "chunk-results.jsonl"
    first = {"chunk_index": 0, "candidate_ids": ["cell:a", "cell:b"]}
    second = {"chunk_index": 1, "candidate_ids": ["cell:c"]}
    append_jsonl(path, [first])
    append_jsonl(path, [second])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [first, second]
    assert sha256_file(path)


def test_candidate_dispositions_keep_dispatched_work_unknown(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "candidate-plan.jsonl").write_text(
        "".join(json.dumps({"candidate_id": f"cell:{letter}"}) + "\n" for letter in "abc")
    )
    (output / "chunk-results.jsonl").write_text(
        json.dumps(
            {
                "chunk_index": 0,
                "candidates": [
                    {"candidate_id": "cell:a", "action": "exact_scored", "input_index": 0}
                ],
            }
        )
        + "\n"
    )
    (output / "checkpoint.json").write_text(json.dumps({"pool_timings": {"input_count": 2}}))

    counts = write_candidate_dispositions(output, externally_interrupted=True)
    rows = [
        json.loads(line)
        for line in (output / "candidate-dispositions.jsonl").read_text().splitlines()
    ]

    assert counts == {
        "committed": 1,
        "failed": 0,
        "failed_unattributed": 0,
        "not_selected": 0,
        "dispatched_uncommitted_or_unknown": 2,
        "not_dispatched": 0,
        "unvisited_after_worker_exit": 0,
    }
    assert [row["status"] for row in rows] == [
        "exact_scored",
        "worker_state_unknown_at_interruption",
        "worker_state_unknown_at_interruption",
    ]


def test_pool_failure_keeps_known_failure_and_unknown_remainder(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "candidate-plan.jsonl").write_text(
        "".join(json.dumps({"candidate_id": f"cell:{letter}"}) + "\n" for letter in "abc")
    )

    counts = write_candidate_dispositions(
        output,
        externally_interrupted=True,
        failed_candidate_ids=["cell:b"],
        failed_count_unattributed=1,
    )
    rows = [
        json.loads(line)
        for line in (output / "candidate-dispositions.jsonl").read_text().splitlines()
    ]

    assert counts["failed"] == 1
    assert counts["dispatched_uncommitted_or_unknown"] == 2
    assert [row["status"] for row in rows] == [
        "worker_state_unknown_at_interruption",
        "worker_reported_failure",
        "worker_state_unknown_at_interruption",
    ]


def test_prefix_completion_is_success_without_full_domain_certificate() -> None:
    assert (
        worker_exit_code(
            {
                "totals": {
                    "selected_request_complete": True,
                    "all_candidates_accounted": False,
                    "finite_candidate_set_certified": False,
                }
            }
        )
        == 0
    )
    assert worker_exit_code({"totals": {"selected_request_complete": False}}) == 1


def test_resolve_project_root_uses_git_common_dir(tmp_path: Path, monkeypatch) -> None:
    shared_root = tmp_path / "repo"
    (shared_root / ".git").mkdir(parents=True)
    (shared_root / ".agent-work").mkdir()
    worktree = shared_root / ".agent-work" / "worktrees" / "worker"
    worktree.mkdir(parents=True)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0, stdout=str(shared_root / ".git") + "\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_project_root(worktree) == shared_root


def test_pool_failure_preserves_not_dispatched_ids(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "candidate-plan.jsonl").write_text(
        "".join(json.dumps({"candidate_id": f"cell:{letter}"}) + "\n" for letter in "abc")
    )
    counts = write_candidate_dispositions(
        output,
        externally_interrupted=True,
        failed_candidate_ids=["cell:b"],
        not_dispatched_candidate_ids=["cell:c"],
        failed_count_unattributed=1,
    )
    rows = [
        json.loads(line)
        for line in (output / "candidate-dispositions.jsonl").read_text().splitlines()
    ]
    assert counts["failed"] == 1
    assert counts["not_dispatched"] == 1
    assert counts["dispatched_uncommitted_or_unknown"] == 1
    assert [row["status"] for row in rows] == [
        "worker_state_unknown_at_interruption",
        "worker_reported_failure",
        "not_dispatched",
    ]


def test_finite_domain_driver_refuses_nonempty_output(tmp_path: Path) -> None:
    from tools.run_a26_cached_finite_domain import run_finite_domain

    output = tmp_path / "out"
    output.mkdir()
    (output / "old.jsonl").write_text("{}\n")
    with pytest.raises(FileExistsError, match="Fresh finite-domain"):
        run_finite_domain(
            generation_root=tmp_path,
            scoring_root=tmp_path,
            expected_generation_source_sha="0" * 40,
            expected_scoring_source_sha="0" * 40,
            static_root=tmp_path,
            cache_path=tmp_path / "cache",
            generation_run_root=tmp_path,
            output_root=output,
        )


def test_actual_scoring_tree_subprocess_import_when_configured() -> None:
    scoring_root = os.environ.get("A26_SCORING_ROOT")
    scoring_sha = os.environ.get("A26_SCORING_SHA")
    if not scoring_root or not scoring_sha:
        pytest.skip("set A26_SCORING_ROOT and A26_SCORING_SHA for the pinned-source smoke")
    assert smoke_scoring_source(Path(scoring_root), scoring_sha) == scoring_sha


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="Linux process-state check")
def test_external_deadline_cleanup_kills_and_reaps_grandchild() -> None:
    child_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(p.pid,flush=True); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert child.stdout is not None
    grandchild_pid = int(child.stdout.readline().strip())
    terminate_process_group(child, grace_s=0.1)
    assert child.poll() is not None

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            stat_text = Path(f"/proc/{grandchild_pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            break
        state = stat_text[stat_text.rfind(")") + 2 :].split()[0]
        if state == "Z":
            break
        time.sleep(0.02)
    else:
        pytest.fail("process-group cleanup left a running grandchild")


def test_timeout_inventory_recovery_reads_complete_verified_cache_ids(tmp_path: Path) -> None:
    import hashlib

    from tools.run_a26_cached_finite_domain import (
        ensure_inventory_from_verified_cache,
        sha256_tree,
    )

    cache = tmp_path / "cache"
    cache.mkdir()
    core = {"proposal_count": 2, "proposal_ids": ["a", "b"]}
    canonical = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    (cache / "metadata.json").write_text(
        json.dumps(
            {
                "metadata": core,
                "metadata_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            }
        )
    )
    (cache / "payload.npz").write_bytes(b"payload")
    generation = tmp_path / "generation"
    generation.mkdir()
    (generation / "run-manifest.json").write_text(
        json.dumps(
            {
                "process_result": {"status": "completed"},
                "cache_path": str(cache.resolve()),
                "cache_tree_sha256": sha256_tree(cache),
            }
        )
    )
    output = tmp_path / "output"
    output.mkdir()

    assert ensure_inventory_from_verified_cache(output, cache, generation) == 2
    rows = [
        json.loads(line) for line in (output / "candidate-inventory.jsonl").read_text().splitlines()
    ]
    assert [row["candidate_id"] for row in rows] == ["cell:a", "cell:b"]


def test_runtime_config_binding_rejects_search_setting_drift() -> None:
    from types import SimpleNamespace

    from tools.run_a26_cached_finite_domain import validate_runtime_config_binding

    config = SimpleNamespace(
        algorithm="wedge_geodesic",
        max_range_m=10000.0,
        min_range_m=0.0,
        range_inclusive=True,
        use_types=False,
        generated_sigma_deg=0.5,
        sigma_floor_deg=0.05,
        proposal_source_pair_budget=1,
        proposal_cell_budget=5_000_000,
        proposal_chunk_size=4096,
        proposal_solver_tolerance_deg=1e-7,
        bounds_xy=(-1.0, 1.0, -2.0, 2.0),
    )
    measurements = SimpleNamespace(measurements=[SimpleNamespace(sigma_deg=0.5)])
    catalog = SimpleNamespace(records=[object()])
    vector_map = SimpleNamespace(crs=SimpleNamespace(to_string=lambda: "EPSG:32618"))
    generation = {
        "algorithm": "wedge_geodesic",
        "max_range_m": 10000.0,
        "min_range_m": 0.0,
        "range_inclusive": True,
        "use_types": False,
        "generated_sigma_deg": 0.5,
        "sigma_floor_deg": 0.05,
        "effective_observation_sigmas_deg": [[0, 0.5]],
        "bounds_xy": [-1.0, 1.0, -2.0, 2.0],
        "map_projected_crs": "EPSG:32618",
        "proposal_source_pair_budget_configured": 1,
        "max_source_pairs": 1,
        "max_candidate_cells": 5_000_000,
        "source_chunk_size": 4096,
        "solver_tolerance_deg": 1e-7,
        "cell_size_m": 250.0,
        "angular_tolerance_deg": 0.5,
    }
    validate_runtime_config_binding(config, generation, measurements, catalog, vector_map)
    generation["use_types"] = True
    with pytest.raises(ValueError, match="use_types"):
        validate_runtime_config_binding(config, generation, measurements, catalog, vector_map)


def test_prefix_profile_marks_unselected_candidates_without_certifying_domain(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    plan = [{"candidate_id": f"cell:{letter}"} for letter in "abc"]
    (output / "candidate-plan.jsonl").write_text("".join(json.dumps(row) + "\n" for row in plan))
    (output / "candidate-inventory.jsonl").write_text(
        "".join(json.dumps({"candidate_id": row["candidate_id"]}) + "\n" for row in plan)
    )
    (output / "chunk-results").mkdir()
    (output / "chunk-results/chunk-00000000.json").write_text(
        json.dumps(
            {
                "chunk_index": 0,
                "candidates": [
                    {"candidate_id": "cell:a", "action": "exact_scored", "input_index": 0}
                ],
            }
        )
    )

    counts = write_candidate_dispositions(
        output, selected_candidate_ids={"cell:a"}, candidate_prefix_limit=1
    )
    rows = [
        json.loads(line)
        for line in (output / "candidate-dispositions.jsonl").read_text().splitlines()
    ]
    assert counts["committed"] == 1
    assert counts["not_selected"] == 2
    assert [row["status"] for row in rows] == [
        "exact_scored",
        "not_selected_by_explicit_prefix_profile",
        "not_selected_by_explicit_prefix_profile",
    ]
