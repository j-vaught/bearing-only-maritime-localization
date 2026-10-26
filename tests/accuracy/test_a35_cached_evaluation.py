from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.evaluate_cached_finite_domain import (  # noqa: E402
    canonical_sha256,
    geodesic_error_m,
    load_run,
    sha256_file,
    sha256_tree,
)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def _fixture(root: Path, *, incomplete: bool = False) -> tuple[Path, dict[str, Any]]:
    plan = [
        {"candidate_id": "cell:1:2", "input_index": 0, "position_xy_m": [500000.0, 4900000.0]},
        {"candidate_id": "cell:1:3", "input_index": 1, "position_xy_m": [500250.0, 4900000.0]},
    ]
    _write_jsonl(root / "candidate-plan.jsonl", plan)
    _write_jsonl(
        root / "candidate-inventory.jsonl", [{"candidate_id": row["candidate_id"]} for row in plan]
    )
    chunk_root, hyp_root = root / "chunk-results", root / "hypotheses"
    candidate_rows = [
        {"candidate_id": "cell:1:2", "input_index": 0, "action": "exact_scored", "score": 2.0}
    ]
    hyp_rows = [
        {
            "input_index": 0,
            "hypothesis": {
                "east_m": 500000.0,
                "north_m": 4900000.0,
                "score": 2.0,
                "assignments": [
                    {
                        "report_index": 0,
                        "catalog_key": "catalog:a",
                        "residual_deg": 0.1,
                        "cost": 1.0,
                    }
                ],
                "score_components": {
                    "matched_cost": 1.0,
                    "clutter_cost": 1.0,
                    "missed_cost": 0.0,
                    "total_cost": 2.0,
                },
            },
        }
    ]
    if not incomplete:
        candidate_rows.append(
            {"candidate_id": "cell:1:3", "input_index": 1, "action": "exact_scored", "score": 2.0}
        )
        hyp_rows.append(
            {
                "input_index": 1,
                "hypothesis": {
                    "east_m": 500250.0,
                    "north_m": 4900000.0,
                    "score": 2.0,
                    "assignments": [],
                    "score_components": {
                        "matched_cost": 0.0,
                        "clutter_cost": 2.0,
                        "missed_cost": 0.0,
                        "total_cost": 2.0,
                    },
                },
            }
        )
    _write(
        chunk_root / "chunk-00000000.json",
        {"chunk_index": 0, "candidate_count": len(candidate_rows), "candidates": candidate_rows},
    )
    _write(hyp_root / "chunk-00000000.json", hyp_rows)
    identity: dict[str, Any] = {
        "schema": "a26-cached-finite-domain-identity-v2",
        "generation_source_sha": "1" * 40,
        "scoring_source_sha": "2" * 40,
        "proposal_generation_run_id": "cache-run",
        "proposal_cache_tree_sha256": "3" * 64,
        "proposal_cache_context_sha256": "4" * 64,
        "static_input_sha256": {"measurements": "5" * 64, "catalog": "6" * 64, "map": "7" * 64},
        "scoring_config_sha256": "8" * 64,
        "candidate_order": "cli_geodesic_matching_votes_water_residual_evidence_cell_v1",
        "candidate_plan_sha256": sha256_file(root / "candidate-plan.jsonl"),
        "candidate_inventory_sha256": sha256_file(root / "candidate-inventory.jsonl"),
        "candidate_domain_count": 2,
        "execution_mode": "exact",
    }
    identity_sha = canonical_sha256(identity)
    _write(root / "run-identity.json", {**identity, "identity_sha256": identity_sha})
    _write_jsonl(
        root / "candidate-dispositions.jsonl",
        [
            {"candidate_id": "cell:1:2", "status": "exact_scored"},
            {
                "candidate_id": "cell:1:3",
                "status": "worker_state_unknown_at_interruption" if incomplete else "exact_scored",
            },
        ],
    )
    static_hashes = identity["static_input_sha256"]
    manifest = {
        "schema_version": 1,
        "run_id": "a26-cached-finite-domain-exact-v2",
        "generation_source_sha": identity["generation_source_sha"],
        "scoring_source_sha": identity["scoring_source_sha"],
        "proposal_cache_tree_sha256": identity["proposal_cache_tree_sha256"],
        "proposal_cache_context_sha256": identity["proposal_cache_context_sha256"],
        "static_input_sha256": static_hashes,
        "scoring_config_sha256": identity["scoring_config_sha256"],
        "candidate_order": identity["candidate_order"],
        "candidate_plan_sha256": identity["candidate_plan_sha256"],
        "candidate_inventory_sha256": identity["candidate_inventory_sha256"],
        "execution_mode": "exact",
        "run_identity": identity,
        "run_identity_sha256": identity_sha,
        "chunk_results_sha256": sha256_tree(chunk_root),
        "hypotheses_sha256": sha256_tree(hyp_root),
        "totals": {
            "all_candidates_accounted": not incomplete,
            "selected_request_complete": not incomplete,
            "completed_candidate_count": len(candidate_rows),
            "failed_candidate_count": 0,
            "unvisited_candidate_count": 2 - len(candidate_rows),
        },
    }
    _write(root / "run-manifest.json", manifest)
    expected = {
        "generation_source_sha": identity["generation_source_sha"],
        "scoring_source_sha": identity["scoring_source_sha"],
        "scoring_config_sha256": identity["scoring_config_sha256"],
        "proposal_cache_tree_sha256": identity["proposal_cache_tree_sha256"],
        "proposal_cache_context_sha256": identity["proposal_cache_context_sha256"],
        "static_input_sha256": static_hashes,
        "candidate_order": identity["candidate_order"],
    }
    return root, expected


def test_utm18_and_utm19_coordinates_of_same_point_have_zero_geodesic_error() -> None:
    lon, lat = -68.8895186652, 44.4553931460
    xy18 = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(lon, lat)
    xy19 = Transformer.from_crs("EPSG:4326", "EPSG:32619", always_xy=True).transform(lon, lat)
    assert geodesic_error_m(xy18, xy19, candidate_crs="EPSG:32618", truth_crs="EPSG:32619") < 1e-6


def test_equal_scores_rank_by_global_input_index_and_incomplete_stays_incomplete(
    tmp_path: Path,
) -> None:
    root, expected = _fixture(tmp_path / "run")
    loaded = load_run(root, expected_bindings=expected)
    assert [item.input_index for item in loaded.candidates] == [0, 1]
    assert loaded.complete
    partial_root, expected = _fixture(tmp_path / "partial", incomplete=True)
    partial = load_run(partial_root, expected_bindings=expected)
    assert [item.input_index for item in partial.candidates] == [0]
    assert not partial.complete
    assert partial.disposition_counts["worker_state_unknown_at_interruption"] == 1


def test_disposition_must_match_durable_chunk_action(tmp_path: Path) -> None:
    root, expected = _fixture(tmp_path / "run")
    rows = [
        {"candidate_id": "cell:1:2", "status": "pruned"},
        {"candidate_id": "cell:1:3", "status": "exact_scored"},
    ]
    _write_jsonl(root / "candidate-dispositions.jsonl", rows)
    with pytest.raises(ValueError, match="differs from its durable chunk action"):
        load_run(root, expected_bindings=expected)


def test_missing_or_corrupt_chunk_sidecar_fails_closed(tmp_path: Path) -> None:
    root, expected = _fixture(tmp_path / "run")
    (root / "hypotheses" / "chunk-00000000.json").unlink()
    with pytest.raises(ValueError, match="tree hash mismatch|Missing hypothesis sidecar"):
        load_run(root, expected_bindings=expected)
    root, expected = _fixture(tmp_path / "other")
    _write(root / "hypotheses" / "chunk-00000000.json", [{"input_index": 9, "hypothesis": {}}])
    with pytest.raises(ValueError, match="tree hash mismatch"):
        load_run(root, expected_bindings=expected)


def test_duplicate_ids_across_continuation_chunks_are_rejected(tmp_path: Path) -> None:
    root, expected = _fixture(tmp_path / "run", incomplete=True)
    _write(
        root / "chunk-results" / "chunk-00000001.json",
        {
            "chunk_index": 1,
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_id": "cell:1:2",
                    "input_index": 0,
                    "action": "exact_scored",
                    "score": 2.0,
                }
            ],
        },
    )
    _write(root / "hypotheses" / "chunk-00000001.json", [])
    manifest = json.loads((root / "run-manifest.json").read_text())
    manifest["chunk_results_sha256"] = sha256_tree(root / "chunk-results")
    manifest["hypotheses_sha256"] = sha256_tree(root / "hypotheses")
    _write(root / "run-manifest.json", manifest)
    with pytest.raises(ValueError, match="more than once"):
        load_run(root, expected_bindings=expected)


@pytest.mark.parametrize(
    "hypothesis",
    [
        {"east_m": 1.0, "north_m": 2.0, "score": float("nan"), "assignments": []},
        {"east_m": 1.0, "north_m": 2.0, "assignments": []},
    ],
)
def test_nonfinite_or_missing_hypothesis_score_is_rejected(hypothesis: dict[str, Any]) -> None:
    from tools.evaluate_cached_finite_domain import decode_hypothesis

    with pytest.raises(ValueError):
        decode_hypothesis(hypothesis)
