from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from maritime_localization_shared.contracts.localization import Hypothesis  # noqa: E402
from tools.refine_cached_modes import (  # noqa: E402
    Seed,
    canonical_hash,
    load_committed,
    read_json,
    select_modes,
    sha256,
    tree_hash,
    verify_selected_plan,
)


def seed(x, y, score, index=0):
    return Seed(
        index,
        f"cell:cell:{x}:{y}",
        Hypothesis(x * 250.0, y * 250.0, score),
        "chunk-00000000.json",
        "a" * 64,
        "b" * 64,
    )


def test_selection_local_minima_cap_and_shuffle():
    rows = [seed(0, 0, 2), seed(1, 0, 1, 1), seed(4, 4, 0, 2), seed(8, 8, 3, 3)]
    assert [s.input_index for s in select_modes(rows, cap=2)] == [2, 1]
    assert select_modes(rows) == select_modes(list(reversed(rows)))


def test_equal_score_ties_have_deterministic_coordinate_order():
    a, b = seed(0, 0, -2), seed(1, 0, -2, 1)
    assert select_modes([b, a]) == [a]


def test_mode_separation_and_duplicate_cells():
    a, b = seed(0, 0, 1), seed(4, 0, 2, 1)
    assert select_modes([a, b], separation_m=1100) == [a]
    with pytest.raises(ValueError, match="Repeated"):
        select_modes([a, a])


@pytest.mark.parametrize("cap,separation", [(0, 100), (True, 100), (2, -1), (2, float("nan"))])
def test_invalid_selection_settings(cap, separation):
    with pytest.raises(ValueError):
        select_modes([], cap, separation)


def fixture(root: Path):
    (root / "chunk-results").mkdir()
    (root / "hypotheses").mkdir()
    s = seed(1, 2, 3)
    plan = {"input_index": 0, "candidate_id": s.candidate_id, "position_xy_m": [250.0, 500.0]}
    for name in (
        "candidate-plan.jsonl",
        "candidate-inventory.jsonl",
        "candidate-dispositions.jsonl",
    ):
        (root / name).write_text(json.dumps(plan) + "\n")
    side = root / "hypotheses" / s.chunk_name
    side.write_text(json.dumps([{"input_index": 0, "hypothesis": asdict(s.hypothesis)}]))
    chunk = {
        "chunk_index": 0,
        "hypotheses_sha256": sha256(side),
        "candidates": [{**plan, "action": "exact_scored", "score": 3.0}],
    }
    (root / "chunk-results" / s.chunk_name).write_text(json.dumps(chunk))
    identity = {"schema": "test"}
    identity["identity_sha256"] = canonical_hash(identity)
    (root / "run-identity.json").write_text(json.dumps(identity))
    supervisor = {
        "run_identity_sha256": identity["identity_sha256"],
        "candidate_plan_sha256": sha256(root / "candidate-plan.jsonl"),
        "candidate_inventory_sha256": sha256(root / "candidate-inventory.jsonl"),
        "candidate_dispositions_sha256": sha256(root / "candidate-dispositions.jsonl"),
        "chunk_results_sha256": tree_hash(root / "chunk-results"),
        "hypotheses_sha256": tree_hash(root / "hypotheses"),
    }
    (root / "supervisor-manifest.json").write_text(json.dumps(supervisor))
    return s, supervisor


def test_committed_load_and_plan_binding(tmp_path):
    _, _manifest = fixture(tmp_path)
    seeds, bindings = load_committed(tmp_path)
    assert len(seeds) == 1
    assert bindings["committed_hypothesis_count"] == 1
    verify_selected_plan(tmp_path, seeds)
    (tmp_path / "candidate-plan.jsonl").write_text(
        json.dumps({"input_index": 0, "candidate_id": "wrong", "position_xy_m": [250, 500]}) + "\n"
    )
    with pytest.raises(ValueError, match="immutable"):
        verify_selected_plan(tmp_path, seeds)


def test_orphan_excluded_and_stale_sidecar_rejected(tmp_path):
    _, manifest = fixture(tmp_path)
    orphan = tmp_path / "hypotheses" / "chunk-00000001.json"
    orphan.write_text("[]")
    manifest["hypotheses_sha256"] = tree_hash(tmp_path / "hypotheses")
    (tmp_path / "supervisor-manifest.json").write_text(json.dumps(manifest))
    seeds, bindings = load_committed(tmp_path)
    assert len(seeds) == 1
    assert bindings["orphan_sidecars_excluded"] == [orphan.name]
    (tmp_path / "hypotheses" / "chunk-00000000.json").write_text("[]")
    manifest["hypotheses_sha256"] = tree_hash(tmp_path / "hypotheses")
    (tmp_path / "supervisor-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Sidecar"):
        load_committed(tmp_path)


def test_nonfinite_json_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"score":NaN}')
    with pytest.raises(ValueError, match="Nonfinite"):
        read_json(path)
