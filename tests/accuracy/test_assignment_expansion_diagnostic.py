from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bearing_localization.search.assignment_expansion import ExpansionConfig
from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from tools.run_assignment_expansion_diagnostic import (
    FLOOR_BYTES,
    MAX_OUTPUT_BYTES,
    MODE_COUNT,
    DiagnosticInputError,
    _alarm,
    canonical_lock_path,
    check_resources,
    choose_best_start,
    load_assigned_bundle,
    package_provenance,
    run,
)


def hypothesis(score: float) -> Hypothesis:
    return Hypothesis(
        1.0,
        2.0,
        score,
        assignments=(Assignment(0, "catalog:one", residual_deg=0.1, cost=0.2),),
    )


def assigned_row(index: int = 0) -> dict:
    return {
        "mode_index": index,
        "input_index": index,
        "candidate_id": f"cell:cell:{index}:0",
        "starts": [
            {"label": "cached_hypothesis", "final_hypothesis": asdict(hypothesis(2.0))},
            {"label": "refined_mode.hypothesis", "final_hypothesis": asdict(hypothesis(1.0))},
        ],
    }


def make_bundle(root: Path, count: int = MODE_COUNT) -> tuple[Path, Path]:
    a20 = root / "a20"
    assigned = root / "assigned"
    a20.mkdir()
    assigned.mkdir()
    a20_manifest = {"package_commit": "source", "rows_sha256": "0" * 64}
    (a20 / "manifest.json").write_text(json.dumps(a20_manifest))
    rows_path = assigned / "rows.jsonl"
    rows_path.write_text("\n".join(json.dumps(assigned_row(i)) for i in range(count)) + "\n")
    rows_hash = hashlib.sha256(rows_path.read_bytes()).hexdigest()
    a20_manifest["rows_sha256"] = "rows"
    (a20 / "manifest.json").write_text(json.dumps(a20_manifest))
    assigned_manifest = {
        "schema": "assigned-bearing-refinement-diagnostic-v1",
        "status": "completed",
        "mode_count": count,
        "completed_mode_count": count,
        "rows_file": rows_path.name,
        "rows_sha256": rows_hash,
        "a20_manifest_sha256": hashlib.sha256((a20 / "manifest.json").read_bytes()).hexdigest(),
        "a20_rows_sha256": "rows",
        "package_commit": "harness",
        "harness_commit": "harness",
        "source_pins": {"frozen_scoring_source_sha": "source"},
    }
    (assigned / "manifest.json").write_text(json.dumps(assigned_manifest))
    return assigned, a20


def test_expansion_config_contract_is_frozen() -> None:
    config = ExpansionConfig(local_radius_m=1000.0, max_nfev=100, max_branches=5000)
    assert config.local_radius_m == 1000.0
    assert config.max_nfev == 100
    assert config.max_branches == 5000


def test_malformed_artifact_is_rejected(tmp_path: Path) -> None:
    assigned, a20 = make_bundle(tmp_path)
    manifest = json.loads((assigned / "manifest.json").read_text())
    manifest["schema"] = "wrong"
    (assigned / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DiagnosticInputError, match="schema"):
        load_assigned_bundle(assigned, a20)


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    assigned, a20 = make_bundle(tmp_path)
    manifest = json.loads((assigned / "manifest.json").read_text())
    manifest["rows_sha256"] = "0" * 64
    (assigned / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DiagnosticInputError, match="hash"):
        load_assigned_bundle(assigned, a20)


def test_wrong_assigned_package_pin_is_rejected(tmp_path: Path) -> None:
    assigned, a20 = make_bundle(tmp_path)
    manifest = json.loads((assigned / "manifest.json").read_text())
    manifest["package_commit"] = "wrong"
    (assigned / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DiagnosticInputError, match="pins"):
        load_assigned_bundle(assigned, a20)


def test_missing_64_rows_is_rejected(tmp_path: Path) -> None:
    assigned, a20 = make_bundle(tmp_path, count=63)
    with pytest.raises(DiagnosticInputError, match="64"):
        load_assigned_bundle(assigned, a20)


def test_selected_start_is_deterministic_and_preserves_label_tie_break() -> None:
    row = assigned_row()
    label, selected = choose_best_start(row)
    assert label == "refined_mode.hypothesis"
    assert selected.score == 1.0
    row["starts"][0]["final_hypothesis"] = asdict(hypothesis(1.0))
    assert choose_best_start(row)[0] == "cached_hypothesis"


def test_timeout_propagation_is_explicit() -> None:
    with pytest.raises(TimeoutError, match="deadline"):
        _alarm(0, None)


def test_partial_durable_output_has_explicit_noresume(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    args = argparse.Namespace(timeout_s=1.0, output=output, lock=canonical_lock_path())
    with pytest.raises(FileExistsError, match="resume"):
        run(args)


def test_canonical_lock_path_is_shared_benchmark_lock() -> None:
    lock = canonical_lock_path()
    assert lock.name == "benchmark.lock"
    assert lock.parent.name == ".agent-work"


def test_loaded_package_provenance_is_distinct_from_input_pin() -> None:
    provenance = package_provenance()
    assert provenance["package_source"].endswith("bearing_localization/__init__.py")
    assert len(provenance["package_commit"]) == 40
    assert len(provenance["assignment_expansion_sha256"]) == 64
    assert len(provenance["runner_sha256"]) == 64


def test_disk_reserve_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.run_assignment_expansion_diagnostic.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=FLOOR_BYTES),
    )
    with pytest.raises(OSError, match="reserve"):
        check_resources(tmp_path)


def test_output_cap_has_room_for_all_branch_events() -> None:
    assert MAX_OUTPUT_BYTES == 50 * 1024**2


def test_runner_does_not_import_truth_or_evaluator_modules() -> None:
    tree = ast.parse(
        Path(__file__)
        .parents[2]
        .joinpath("tools/run_assignment_expansion_diagnostic.py")
        .read_text()
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
