"""Evaluator-only before/after audit for A20 cached-mode refinement."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from tools.evaluate_cached_finite_domain import (
    LoadedRun,
    decode_hypothesis,
    geodesic_error_m,
    load_run,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_tree,
)

SCHEMA = "a20-cached-mode-refinement-v1"


def load_refinement(
    root: Path, source_root: Path, run: LoadedRun
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("Unsupported A20 refinement manifest.")
    source_root = source_root.resolve()
    if Path(manifest.get("source_run_root", "")).resolve() != source_root:
        raise ValueError("Refinement points to a different source run.")
    bindings = {
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "source_candidate_plan_sha256": run.manifest.get("candidate_plan_sha256"),
        "source_candidate_inventory_sha256": run.manifest.get("candidate_inventory_sha256"),
        "source_candidate_dispositions_sha256": run.manifest.get("candidate_dispositions_sha256"),
        "source_chunk_results_tree_sha256": sha256_tree(source_root / "chunk-results"),
        "source_hypotheses_tree_sha256": sha256_tree(source_root / "hypotheses"),
        "source_supervisor_manifest_sha256": sha256_file(source_root / "supervisor-manifest.json"),
    }
    for key, value in bindings.items():
        if manifest.get(key) != value:
            raise ValueError(f"Refinement source binding mismatch: {key}.")
    filename = manifest.get("modes_file")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Invalid modes_file path.")
    modes_path = root / filename
    if manifest.get("modes_file_sha256") != sha256_file(modes_path):
        raise ValueError("Mode records hash mismatch.")
    rows = read_jsonl(modes_path)
    counts = (manifest.get("selected_count"), manifest.get("refined_count"), manifest.get("cap"))
    if any(type(x) is not int or x < 0 for x in counts):
        raise ValueError("Invalid refinement counts.")
    selected, refined, cap = counts
    if cap != 64 or selected != len(rows) or selected > cap:
        raise ValueError("Selected modes violate the explicit 64-mode cap.")
    if (
        type(manifest.get("complete")) is not bool
        or type(manifest.get("deadline_reached")) is not bool
    ):
        raise ValueError("Completion flags must be booleans.")
    if manifest["complete"] != (refined == selected and not manifest["deadline_reached"]):
        raise ValueError("Completion flag contradicts mode counts or deadline.")
    shards = manifest.get("selected_source_shard_sha256")
    if not isinstance(shards, dict):
        raise ValueError("Selected source shard bindings are missing.")
    for relative, digest in shards.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or sha256_file(source_root / path) != digest:
            raise ValueError(f"Bad selected source shard binding: {relative}.")
    source_by_index = {item.input_index: item for item in run.candidates}
    seen_indices: set[int] = set()
    seen_modes: set[str] = set()
    successes = 0
    for row in rows:
        index, mode_id = row.get("input_index"), row.get("mode_id")
        if type(index) is not int or index not in source_by_index or index in seen_indices:
            raise ValueError("Invalid or duplicate source input_index.")
        if not isinstance(mode_id, str) or not mode_id or mode_id in seen_modes:
            raise ValueError("Invalid or duplicate mode_id.")
        source = source_by_index[index]
        if row.get("candidate_id") != source.candidate_id:
            raise ValueError("Candidate ID differs from source.")
        chunk = row.get("source_chunk_path")
        if not isinstance(chunk, str) or Path(chunk).is_absolute() or ".." in Path(chunk).parts:
            raise ValueError("Invalid source chunk path.")
        chunk_path = Path(chunk)
        chunk_index = row.get("source_chunk_index")
        if chunk_path.parts[:1] != ("chunk-results",) or type(chunk_index) is not int:
            raise ValueError("Invalid source chunk path or index.")
        if chunk_path.stem != f"chunk-{chunk_index:08d}":
            raise ValueError("Source chunk path and index disagree.")
        if row.get("source_chunk_sha256") != sha256_file(source_root / chunk_path):
            raise ValueError("Source chunk hash mismatch.")
        hyp_shard = f"hypotheses/{chunk_path.name}"
        if hyp_shard not in shards:
            raise ValueError("Selected hypothesis shard is not bound by the manifest.")
        hyp = decode_hypothesis(row.get("source_hypothesis"))
        if hyp != source.hypothesis:
            raise ValueError("Source hypothesis differs from immutable cache.")
        revalidated = row.get("revalidated_hypothesis")
        if revalidated is not None:
            delta = row.get("revalidated_score_delta")
            if (
                isinstance(delta, bool)
                or not isinstance(delta, (int, float))
                or not math.isfinite(delta)
            ):
                raise ValueError("Invalid revalidation score delta.")
            if not math.isclose(
                decode_hypothesis(revalidated).score - hyp.score, float(delta), abs_tol=1e-9
            ):
                raise ValueError("Revalidation delta does not match hypotheses.")
        refined_hyp = row.get("refined_hypothesis")
        if refined_hyp is not None:
            decode_hypothesis(refined_hyp)
            successes += 1
        if type(row.get("iterations")) is not int or row["iterations"] < 0:
            raise ValueError("Invalid iteration count.")
        seen_indices.add(index)
        seen_modes.add(mode_id)
    if successes != refined or manifest.get("selection_order") != [r["mode_id"] for r in rows]:
        raise ValueError("Refined count or deterministic selection order mismatch.")
    return manifest, rows


def _metrics(
    raw: Any, truth_xy: tuple[float, float], ids: dict[int, set[str]]
) -> dict[str, Any] | None:
    if raw is None:
        return None
    hyp = decode_hypothesis(raw)
    correct = sum(a.catalog_key in ids.get(a.report_index, set()) for a in hyp.assignments)
    components = hyp.score_components
    return {
        "score": hyp.score,
        "position_xy_m": [hyp.east_m, hyp.north_m],
        "geodesic_error_m": geodesic_error_m(
            (hyp.east_m, hyp.north_m), truth_xy, candidate_crs="EPSG:32618", truth_crs="EPSG:32619"
        ),
        "assignment_count": len(hyp.assignments),
        "direct_truth_key_assignment_count": correct,
        "assignments": [
            {
                "report_index": a.report_index,
                "catalog_key": a.catalog_key,
                "residual_deg": a.residual_deg,
                "cost": a.cost,
            }
            for a in hyp.assignments
        ],
        "score_components": None
        if components is None
        else {
            "matched_cost": components.matched_cost,
            "clutter_cost": components.clutter_cost,
            "missed_cost": components.missed_cost,
            "total_cost": components.total_cost,
        },
    }


def evaluate_rows(
    rows: list[dict[str, Any]],
    run: LoadedRun,
    *,
    truth_xy: tuple[float, float],
    truth_ids: dict[int, set[str]],
) -> list[dict[str, Any]]:
    source_by_index = {item.input_index: item for item in run.candidates}
    result = []
    for row in rows:
        stages = {
            key: _metrics(row.get(field), truth_xy, truth_ids)
            for key, field in (
                ("source", "source_hypothesis"),
                ("revalidated", "revalidated_hypothesis"),
                ("refined", "refined_hypothesis"),
            )
        }
        source_metrics = stages["source"]
        if source_metrics is None:
            raise ValueError("Missing source hypothesis metrics.")
        if not math.isclose(
            source_metrics["score"],
            source_by_index[row["input_index"]].hypothesis.score,
            abs_tol=1e-9,
        ):
            raise ValueError("Source objective differs from cached hypothesis.")
        result.append(
            {
                "mode_id": row["mode_id"],
                "input_index": row["input_index"],
                "candidate_id": row["candidate_id"],
                "iterations": row["iterations"],
                "stop_reason": row.get("stop_reason"),
                "improved_hypothesis": row.get("improved_hypothesis"),
                "history": row.get("history"),
                "revalidated_score_delta": row.get("revalidated_score_delta"),
                **stages,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "refinement-root", "truth-dir", "expected-bindings", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run = load_run(args.source_root, expected_bindings=read_json(args.expected_bindings))
    manifest, rows = load_refinement(args.refinement_root, args.source_root, run)
    # Evaluator truth is opened only after the search/refinement artifacts verify.
    truth = read_json(args.truth_dir / "truth.json")
    support = read_json(args.truth_dir / "support.json")
    if support.get("supported") is not True or support.get("valid_reference_water") is not True:
        raise ValueError("Frozen evaluator support is invalid.")
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    truth_obj = read_contract(args.truth_dir / "truth.json", EvaluationTruth)
    ids: dict[int, set[str]] = {}
    for obj in truth.get("objects", []):
        if obj.get("observation_index") is not None:
            key = str(obj.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                ids.setdefault(int(obj["observation_index"]), set()).add(key)
    rows_out = evaluate_rows(
        rows, run, truth_xy=(truth_obj.observer_east_m, truth_obj.observer_north_m), truth_ids=ids
    )
    output = {
        "schema": "a35-a20-cached-refinement-evaluation-v1",
        "evaluation_only": True,
        "truth_used_for_refinement": False,
        "accepted_fix_claim": False,
        "source_run_complete": run.complete,
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "refinement_manifest_sha256": sha256_file(args.refinement_root / "manifest.json"),
        "refinement_complete": manifest["complete"],
        "deadline_reached": manifest["deadline_reached"],
        "selected_count": manifest["selected_count"],
        "refined_count": manifest["refined_count"],
        "explicit_mode_cap": manifest["cap"],
        "source_candidate_dispositions": run.disposition_counts,
        "mode_evaluations": rows_out,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
