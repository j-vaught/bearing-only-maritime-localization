"""Evaluator-only before/after audit for a truth-blind refinement of cached modes."""

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


def _hyp(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, dict) and "hypothesis" in raw:
        raw = raw["hypothesis"]
    return decode_hypothesis(raw)


def load_refinement(
    root: Path, source_root: Path, run: LoadedRun
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != "cached-mode-refinement-v1":
        raise ValueError("Unsupported cached-mode refinement manifest.")
    source_root = source_root.resolve()
    if Path(manifest.get("source_attempt", "")).resolve() != source_root:
        raise ValueError("Refinement points to a different source attempt.")
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or identity.get("identity_sha256") != run.manifest.get(
        "run_identity_sha256"
    ):
        raise ValueError("Refinement identity differs from source run.")
    supervisor = source_root / "supervisor-manifest.json"
    if manifest.get("supervisor_sha256") != sha256_file(supervisor):
        raise ValueError("Source supervisor manifest hash mismatch.")
    supervisor_doc = read_json(supervisor)
    expected_source_evidence = {
        field: supervisor_doc.get(field)
        for field in (
            "candidate_plan_sha256",
            "candidate_inventory_sha256",
            "candidate_dispositions_sha256",
            "chunk_results_sha256",
            "hypotheses_sha256",
        )
    }
    if manifest.get("source_evidence_sha256") != expected_source_evidence:
        raise ValueError("Source evidence hash mapping differs from supervisor manifest.")
    if manifest.get("package_commit") != run.identity.get("scoring_source_sha"):
        raise ValueError("Package commit differs from pinned scoring source.")
    for field in (
        "candidate_plan_sha256",
        "candidate_inventory_sha256",
        "candidate_dispositions_sha256",
    ):
        if supervisor_doc.get(field) != run.manifest.get(field):
            raise ValueError(f"Source supervisor binding mismatch: {field}.")
    for directory, key in (
        ("chunk-results", "chunk_results_sha256"),
        ("hypotheses", "hypotheses_sha256"),
    ):
        if supervisor_doc.get(key) != sha256_tree(source_root / directory):
            raise ValueError(f"Source {directory} tree hash mismatch.")
    rows_file = manifest.get("rows_file")
    if not isinstance(rows_file, str) or Path(rows_file).name != rows_file:
        raise ValueError("Invalid rows_file.")
    rows_path = root / rows_file
    if manifest.get("rows_sha256") != sha256_file(rows_path):
        raise ValueError("Refinement rows hash mismatch.")
    rows = read_jsonl(rows_path)
    selected = manifest.get("selected_modes")
    selected_count = manifest.get("selected_mode_count")
    completed = manifest.get("completed_mode_count")
    cap = manifest.get("mode_cap")
    if (
        not isinstance(selected, list)
        or type(selected_count) is not int
        or selected_count != len(selected)
    ):
        raise ValueError("Selected mode count differs from selection records.")
    if type(completed) is not int or completed != len(rows) or completed < 0:
        raise ValueError("Completed mode count differs from durable rows.")
    if type(cap) is not int or cap != 64 or selected_count > cap or completed > selected_count:
        raise ValueError("Explicit mode cap or counts are invalid.")
    if manifest.get("full_domain_complete") is not False:
        raise ValueError("Mode refinement cannot claim full-domain completion.")
    by_index = {item.input_index: item for item in run.candidates}
    selected_by_index: dict[int, dict[str, Any]] = {}
    for seed in selected:
        index = seed.get("input_index")
        if type(index) is not int or index not in by_index or index in selected_by_index:
            raise ValueError("Selected seed index is invalid or duplicated.")
        cached = _hyp(seed.get("cached_hypothesis"))
        source = by_index[index]
        if seed.get("candidate_id") != source.candidate_id or cached != source.hypothesis:
            raise ValueError("Selected cached hypothesis differs from immutable source.")
        name = seed.get("chunk_name")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Invalid source chunk name.")
        chunk_path = source_root / "chunk-results" / name
        sidecar_path = source_root / "hypotheses" / name
        if seed.get("chunk_sha256") != sha256_file(chunk_path):
            raise ValueError("Selected source chunk hash mismatch.")
        if seed.get("sidecar_sha256") != sha256_file(sidecar_path):
            raise ValueError("Selected source hypothesis shard hash mismatch.")
        selected_by_index[index] = seed
    if [row.get("seed_order") for row in rows] != list(range(completed)):
        raise ValueError("Refinement rows are not in contiguous selection order.")
    if [row.get("input_index") for row in rows] != [
        item["input_index"] for item in selected[:completed]
    ]:
        raise ValueError("Refinement rows differ from selected mode order.")
    for row in rows:
        index = row.get("input_index")
        if type(index) is not int or index not in selected_by_index:
            raise ValueError("Refinement row index is not selected.")
        seed = selected_by_index[index]
        if row.get("candidate_id") != seed["candidate_id"]:
            raise ValueError("Refined candidate differs from selected seed.")
        if (
            row.get("chunk_name") != seed["chunk_name"]
            or row.get("chunk_sha256") != seed["chunk_sha256"]
        ):
            raise ValueError("Refined row source chunk differs from manifest.")
        if row.get("sidecar_sha256") != seed["sidecar_sha256"]:
            raise ValueError("Refined row source sidecar differs from manifest.")
        if _hyp(row.get("cached_hypothesis")) != by_index[index].hypothesis:
            raise ValueError("Refined row cached hypothesis differs from source.")
        revalidated = _hyp(row.get("revalidated_hypothesis"))
        if revalidated is not None and (
            not math.isclose(
                revalidated.score, by_index[index].hypothesis.score, rel_tol=1e-10, abs_tol=1e-8
            )
            or (revalidated.east_m, revalidated.north_m)
            != (by_index[index].hypothesis.east_m, by_index[index].hypothesis.north_m)
        ):
            raise ValueError("Revalidated seed differs from cached score or position.")
        mode = row.get("refined_mode")
        if not isinstance(mode, dict):
            raise ValueError("Missing RefinedMode record.")
        for field in ("iterations", "stop_reason", "history", "hypothesis", "improved_hypothesis"):
            if field not in mode:
                raise ValueError(f"RefinedMode is missing {field}.")
        _hyp(mode["hypothesis"])
        if type(mode["improved_hypothesis"]) is not bool:
            raise ValueError("RefinedMode improved_hypothesis must be a boolean.")
        history = mode["history"]
        if not isinstance(history, list) or not history:
            raise ValueError("Refinement history is missing.")
        history_scores = []
        for expected_iteration, item in enumerate(history):
            if not isinstance(item, dict) or item.get("iteration") != expected_iteration:
                raise ValueError("Refinement history iteration sequence is invalid.")
            score = item.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ValueError("Refinement history contains an invalid score.")
            history_scores.append(float(score))
        if any(after > before + 1e-9 for before, after in zip(history_scores, history_scores[1:])):
            raise ValueError("Refinement objective increased in the accepted history.")
        final_hypothesis = _hyp(mode["hypothesis"])
        if final_hypothesis is not None and not math.isclose(
            final_hypothesis.score, history_scores[-1], rel_tol=1e-10, abs_tol=1e-8
        ):
            raise ValueError("Final refined score differs from accepted history.")
    return manifest, rows


def _metrics(
    raw: Any, truth_xy: tuple[float, float], ids: dict[int, set[str]]
) -> dict[str, Any] | None:
    hyp = _hyp(raw)
    if hyp is None:
        return None
    correct = sum(a.catalog_key in ids.get(a.report_index, set()) for a in hyp.assignments)
    comp = hyp.score_components
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
        if comp is None
        else {
            "matched_cost": comp.matched_cost,
            "clutter_cost": comp.clutter_cost,
            "missed_cost": comp.missed_cost,
            "total_cost": comp.total_cost,
        },
    }


def evaluate_rows(
    rows: list[dict[str, Any]],
    run: LoadedRun,
    *,
    truth_xy: tuple[float, float],
    truth_ids: dict[int, set[str]],
) -> list[dict[str, Any]]:
    result = []
    source_rank = {item.input_index: rank for rank, item in enumerate(run.candidates, start=1)}
    for row in rows:
        mode = row["refined_mode"]
        stages = {
            "source": _metrics(row["cached_hypothesis"], truth_xy, truth_ids),
            "revalidated": _metrics(row["revalidated_hypothesis"], truth_xy, truth_ids),
            "refined": _metrics(mode.get("hypothesis"), truth_xy, truth_ids),
        }
        result.append(
            {
                "seed_order": row["seed_order"],
                "source_score_rank": source_rank[row["input_index"]],
                "input_index": row["input_index"],
                "candidate_id": row["candidate_id"],
                "iterations": mode["iterations"],
                "stop_reason": mode["stop_reason"],
                "improved_hypothesis": mode["improved_hypothesis"],
                "mode_history": mode["history"],
                "mode_uncertainty": {
                    k: mode.get(k)
                    for k in (
                        "uncertainty_rank",
                        "uncertainty_singular",
                        "uncertainty_reason",
                        "estimated_95_radius_m",
                    )
                },
                **stages,
            }
        )
    refined_order: list[tuple[float, int]] = []
    for item in result:
        refined = item["refined"]
        index = item["input_index"]
        if isinstance(refined, dict) and type(index) is int:
            refined_order.append((float(refined["score"]), index))
    refined_order.sort()
    refined_rank = {
        input_index: rank for rank, (_, input_index) in enumerate(refined_order, start=1)
    }
    for item in result:
        item["refined_score_rank_within_selected"] = refined_rank.get(item["input_index"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "refinement-root", "truth-dir", "expected-bindings", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run = load_run(args.source_root, expected_bindings=read_json(args.expected_bindings))
    manifest, rows = load_refinement(args.refinement_root, args.source_root, run)
    # Truth is opened only after source and refinement bindings have been verified.
    truth = read_json(args.truth_dir / "truth.json")
    support_path = args.truth_dir / "support.json"
    support = read_json(support_path)
    truth_crs_path = args.truth_dir / "truth-crs.json"
    truth_crs_doc = read_json(truth_crs_path)
    if support.get("supported") is not True or support.get("valid_reference_water") is not True:
        raise ValueError("Frozen evaluator support is invalid.")
    if (
        support.get("port_id") != "port-001"
        or support.get("placement_id") != "placement-001"
        or type(support.get("detected_physical_catalog_matched_groups")) is not int
    ):
        raise ValueError("Frozen evaluator support belongs to a different trial.")
    trial_id = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
    if truth_crs_doc.get("coordinate_reference_system") != "EPSG:32619":
        raise ValueError("Frozen truth must use EPSG:32619.")
    if truth_crs_doc.get("trial_id") not in (None, trial_id):
        raise ValueError("Truth CRS metadata belongs to a different trial.")
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    truth_obj = read_contract(args.truth_dir / "truth.json", EvaluationTruth)
    if truth_obj.trial_id != trial_id or truth.get("trial_id") != trial_id:
        raise ValueError("Truth does not identify the frozen development trial.")
    ids: dict[int, set[str]] = {}
    for obj in truth.get("objects", []):
        if obj.get("observation_index") is not None:
            key = str(obj.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                ids.setdefault(int(obj["observation_index"]), set()).add(key)
    result = {
        "schema": "a35-a20-cached-refinement-evaluation-v1",
        "evaluation_only": True,
        "truth_used_for_refinement": False,
        "accepted_fix_claim": False,
        "source_run_complete": run.complete,
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "refinement_manifest_sha256": sha256_file(args.refinement_root / "manifest.json"),
        "refinement_rows_sha256": manifest.get("rows_sha256"),
        "source_supervisor_manifest_sha256": sha256_file(
            args.source_root / "supervisor-manifest.json"
        ),
        "source_candidate_dispositions_sha256": run.manifest.get("candidate_dispositions_sha256"),
        "source_chunk_results_sha256": run.manifest.get("chunk_results_sha256"),
        "source_hypotheses_sha256": run.manifest.get("hypotheses_sha256"),
        "evaluator_inputs_sha256": {
            "truth": sha256_file(args.truth_dir / "truth.json"),
            "support": sha256_file(support_path),
            "truth_crs": sha256_file(truth_crs_path),
        },
        "refinement_status": manifest.get("status"),
        "refinement_complete": manifest.get("status") == "selected_modes_completed",
        "selected_count": manifest["selected_mode_count"],
        "completed_count": manifest["completed_mode_count"],
        "explicit_mode_cap": manifest["mode_cap"],
        "full_domain_complete": False,
        "source_candidate_dispositions": run.disposition_counts,
        "mode_evaluations": evaluate_rows(
            rows,
            run,
            truth_xy=(truth_obj.observer_east_m, truth_obj.observer_north_m),
            truth_ids=ids,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
