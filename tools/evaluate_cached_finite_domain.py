"""Evaluator-only audit for saved exact-score chunks from a finite-domain run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import Geod, Transformer

from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    ScoreComponents,
    UncertaintyEllipse,
)

_GEOD = Geod(ellps="WGS84")
_EXPECTED_IDENTITY_SCHEMA = "a26-cached-finite-domain-identity-v2"
_EXPECTED_TRIAL_ID = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON constant is forbidden: {value}.")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=_reject_constant)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line, parse_constant=_reject_constant)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object.")
            rows.append(row)
    return rows


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite numeric value.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite.")
    return result


def decode_hypothesis(value: Any) -> Hypothesis:
    """Decode the public localization contract while rejecting permissive JSON numbers."""
    if not isinstance(value, dict):
        raise ValueError("Hypothesis payload must be an object.")
    assignments_raw = value.get("assignments", [])
    if not isinstance(assignments_raw, list):
        raise ValueError("Hypothesis assignments must be an array.")
    assignments = []
    for item in assignments_raw:
        if not isinstance(item, dict):
            raise ValueError("Assignment must be an object.")
        index = item.get("report_index")
        if type(index) is not int:
            raise ValueError("Assignment report_index must be an integer.")
        assignments.append(
            Assignment(
                report_index=index,
                catalog_key=item.get("catalog_key"),
                residual_deg=(
                    None
                    if item.get("residual_deg") is None
                    else _number(item["residual_deg"], "assignment residual")
                ),
                cost=None if item.get("cost") is None else _number(item["cost"], "assignment cost"),
            )
        )
    components_raw = value.get("score_components")
    components = None
    if components_raw is not None:
        if not isinstance(components_raw, dict):
            raise ValueError("score_components must be an object or null.")
        components = ScoreComponents(
            matched_cost=_number(components_raw.get("matched_cost"), "matched_cost"),
            clutter_cost=_number(components_raw.get("clutter_cost"), "clutter_cost"),
            missed_cost=_number(components_raw.get("missed_cost"), "missed_cost"),
            total_cost=_number(components_raw.get("total_cost"), "total_cost"),
        )
    ellipse_raw = value.get("uncertainty")
    ellipse = None
    if ellipse_raw is not None:
        if not isinstance(ellipse_raw, dict):
            raise ValueError("uncertainty must be an object or null.")
        ellipse = UncertaintyEllipse(
            major_radius_m=_number(ellipse_raw.get("major_radius_m"), "major_radius_m"),
            minor_radius_m=_number(ellipse_raw.get("minor_radius_m"), "minor_radius_m"),
            orientation_deg=_number(ellipse_raw.get("orientation_deg"), "orientation_deg"),
            confidence_level=_number(ellipse_raw.get("confidence_level", 0.95), "confidence_level"),
        )
    return Hypothesis(
        east_m=_number(value.get("east_m"), "east_m"),
        north_m=_number(value.get("north_m"), "north_m"),
        score=_number(value.get("score"), "score"),
        assignments=tuple(assignments),
        uncertainty=ellipse,
        score_components=components,
    )


@dataclass(frozen=True)
class SavedCandidate:
    input_index: int
    candidate_id: str
    action: str
    hypothesis: Hypothesis


@dataclass(frozen=True)
class LoadedRun:
    manifest: dict[str, Any]
    identity: dict[str, Any]
    candidates: tuple[SavedCandidate, ...]
    disposition_counts: dict[str, int]
    status_counts: dict[str, int]
    complete: bool


def validate_identity(output_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    identity_path = output_root / "run-identity.json"
    envelope = read_json(identity_path)
    if not isinstance(envelope, dict):
        raise ValueError("run-identity.json must contain an object.")
    identity = dict(envelope)
    declared = identity.pop("identity_sha256", None)
    if identity.get("schema") != _EXPECTED_IDENTITY_SCHEMA:
        raise ValueError("Unsupported cached finite-domain identity schema.")
    if declared != canonical_sha256(identity):
        raise ValueError("Run identity digest does not match its canonical payload.")
    if manifest.get("run_identity") != identity:
        raise ValueError("Run manifest identity differs from run-identity.json.")
    if manifest.get("run_identity_sha256") != declared:
        raise ValueError("Run manifest identity digest differs from run-identity.json.")
    for identity_key, manifest_key in (
        ("candidate_plan_sha256", "candidate_plan_sha256"),
        ("candidate_inventory_sha256", "candidate_inventory_sha256"),
        ("candidate_inventory_sha256", "candidate_inventory_sha256"),
        ("static_input_sha256", "static_input_sha256"),
        ("generation_source_sha", "generation_source_sha"),
        ("scoring_source_sha", "scoring_source_sha"),
        ("scoring_config_sha256", "scoring_config_sha256"),
        ("proposal_cache_tree_sha256", "proposal_cache_tree_sha256"),
        ("proposal_cache_context_sha256", "proposal_cache_context_sha256"),
        ("candidate_order", "candidate_order"),
        ("execution_mode", "execution_mode"),
    ):
        if manifest.get(manifest_key) != identity.get(identity_key):
            raise ValueError(f"Run manifest {manifest_key} is not bound to the run identity.")
    return identity


def verify_expected_bindings(manifest: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    """Require caller-pinned values for every source and input binding."""
    required = {
        "generation_source_sha",
        "scoring_source_sha",
        "scoring_config_sha256",
        "proposal_cache_tree_sha256",
        "proposal_cache_context_sha256",
        "static_input_sha256",
        "candidate_order",
    }
    if set(expected) != required:
        raise ValueError(f"Expected bindings must have exactly these keys: {sorted(required)}.")
    for key in sorted(required):
        if manifest.get(key) != expected[key]:
            raise ValueError(f"Run manifest does not match caller-pinned {key}.")


def _read_chunks(output_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chunk_root = output_root / "chunk-results"
    hypothesis_root = output_root / "hypotheses"
    if not chunk_root.is_dir() or not hypothesis_root.is_dir():
        raise ValueError("Both chunk-results and hypotheses directories are required.")
    chunk_paths = sorted(chunk_root.glob("chunk-*.json"))
    hypothesis_paths = {path.name: path for path in hypothesis_root.glob("chunk-*.json")}
    if not chunk_paths:
        return [], []
    chunk_records = []
    hyp_records = []
    seen_chunk_indices: set[int] = set()
    for path in chunk_paths:
        chunk = read_json(path)
        if not isinstance(chunk, dict) or type(chunk.get("chunk_index")) is not int:
            raise ValueError(f"Invalid chunk record: {path}.")
        index = chunk["chunk_index"]
        if index in seen_chunk_indices:
            raise ValueError(f"Duplicate chunk index {index}.")
        seen_chunk_indices.add(index)
        sidecar_path = hypothesis_paths.get(path.name)
        if sidecar_path is None:
            raise ValueError(f"Missing hypothesis sidecar for {path.name}.")
        if chunk.get("hypotheses_sha256") is not None and chunk.get(
            "hypotheses_sha256"
        ) != sha256_file(sidecar_path):
            raise ValueError(f"Hypothesis sidecar hash mismatch: {sidecar_path}.")
        sidecar = read_json(sidecar_path)
        if not isinstance(sidecar, list):
            raise ValueError(f"Hypothesis sidecar must be a list: {sidecar_path}.")
        chunk_records.append(chunk)
        hyp_records.extend(sidecar)
    if set(hypothesis_paths) != {path.name for path in chunk_paths}:
        raise ValueError("Hypothesis sidecars contain missing or orphaned chunk files.")
    return chunk_records, hyp_records


def load_run(output_root: Path, *, expected_bindings: Mapping[str, Any] | None = None) -> LoadedRun:
    """Load immutable chunk evidence, retaining incompleteness without upgrade."""
    output_root = output_root.resolve()
    manifest_path = output_root / "run-manifest.json"
    supervisor_path = output_root / "supervisor-manifest.json"
    has_run_manifest = manifest_path.is_file()
    if has_run_manifest:
        manifest = read_json(manifest_path)
    elif supervisor_path.is_file():
        supervisor = read_json(supervisor_path)
        envelope = read_json(output_root / "run-identity.json")
        identity = dict(envelope)
        identity_sha = identity.pop("identity_sha256", None)
        if identity.get("schema") != _EXPECTED_IDENTITY_SCHEMA:
            raise ValueError("Unsupported cached finite-domain identity schema.")
        if identity_sha != canonical_sha256(identity):
            raise ValueError("Run identity digest does not match its canonical payload.")
        if supervisor.get("run_identity_sha256") not in (None, identity_sha):
            raise ValueError("Supervisor manifest refers to another run identity.")
        manifest = {
            **identity,
            **supervisor,
            "run_identity": identity,
            "run_identity_sha256": identity_sha,
        }
    else:
        raise FileNotFoundError("A run or supervisor manifest is required.")
    if not isinstance(manifest, dict):
        raise ValueError("Run manifest must be an object.")
    identity = (
        validate_identity(output_root, manifest)
        if has_run_manifest
        else dict(manifest["run_identity"])
    )
    if expected_bindings is not None:
        verify_expected_bindings(manifest, expected_bindings)
    for path, key in (
        (output_root / "candidate-plan.jsonl", "candidate_plan_sha256"),
        (output_root / "candidate-inventory.jsonl", "candidate_inventory_sha256"),
    ):
        if not path.is_file():
            raise ValueError(f"Required bound file is missing: {path.name}.")
        if manifest.get(key, identity.get(key)) != sha256_file(path):
            raise ValueError(f"{path.name} hash does not match its manifest binding.")
    for directory, key in (
        (output_root / "chunk-results", "chunk_results_sha256"),
        (output_root / "hypotheses", "hypotheses_sha256"),
    ):
        declared = manifest.get(key)
        if declared is not None and (not directory.is_dir() or declared != sha256_tree(directory)):
            raise ValueError(f"{directory.name} tree hash mismatch.")
    resume_source = output_root / "resume-source.json"
    lineage = manifest.get("resume_lineage")
    if resume_source.is_file():
        source = read_json(resume_source)
        if not isinstance(source, dict) or not isinstance(lineage, dict):
            raise ValueError("Resume source and run-manifest lineage must both be present.")
        source_root = Path(source.get("source_run_root", "")).resolve()
        if source_root.parent != output_root.parent:
            raise ValueError("Resume source must be an adjacent immutable attempt directory.")
        if lineage.get("source_run_root") != str(source_root):
            raise ValueError("Resume lineage source path differs from resume-source.json.")
        if source.get("source_identity_sha256") != manifest.get("run_identity_sha256"):
            raise ValueError("Resume source identity does not match this run identity.")
        if lineage.get("source_identity_sha256") != source.get("source_identity_sha256"):
            raise ValueError("Resume lineage identity digest differs from resume-source.json.")
        parent_identity = read_json(source_root / "run-identity.json")
        parent_identity_sha = parent_identity.get("identity_sha256")
        parent_core = dict(parent_identity)
        parent_core.pop("identity_sha256", None)
        if parent_identity_sha != canonical_sha256(parent_core):
            raise ValueError("Resume parent identity digest is invalid.")
        if parent_identity_sha != source.get("source_identity_sha256"):
            raise ValueError("Resume parent identity differs from the bound lineage.")
        parent_manifest = source_root / "run-manifest.json"
        parent_supervisor = source_root / "supervisor-manifest.json"
        present_manifest = (
            parent_supervisor
            if parent_supervisor.is_file()
            else (parent_manifest if parent_manifest.is_file() else None)
        )
        declared_parent_manifest_sha = lineage.get("source_run_manifest_sha256")
        actual_parent_manifest_sha = (
            sha256_file(present_manifest) if present_manifest is not None else None
        )
        if declared_parent_manifest_sha != actual_parent_manifest_sha:
            raise ValueError("Resume parent manifest hash differs from the lineage record.")
        if source.get("source_run_manifest_sha256") != declared_parent_manifest_sha:
            raise ValueError("Resume source manifest hash differs from the lineage record.")
        for name, lineage_key in (
            ("chunk-results", "source_chunk_results_sha256"),
            ("hypotheses", "source_hypotheses_sha256"),
        ):
            parent_tree = source_root / name
            expected_tree = lineage.get(lineage_key)
            actual_tree = sha256_tree(parent_tree) if parent_tree.is_dir() else None
            if expected_tree != actual_tree or source.get(lineage_key) != actual_tree:
                raise ValueError(f"Resume parent {name} tree differs from the lineage record.")
    elif lineage is not None:
        raise ValueError("Run manifest declares a resume lineage without resume-source.json.")

    inventory_path = output_root / "candidate-inventory.jsonl"
    inventory = read_jsonl(inventory_path) if inventory_path.is_file() else []
    inventory_ids = [row.get("candidate_id") for row in inventory]
    if any(
        not isinstance(candidate_id, str) or not candidate_id for candidate_id in inventory_ids
    ) or len(inventory_ids) != len(set(inventory_ids)):
        raise ValueError("Candidate inventory contains missing or duplicate IDs.")
    plan_path = output_root / "candidate-plan.jsonl"
    plan = read_jsonl(plan_path) if plan_path.is_file() else []
    plan_by_id: dict[str, dict[str, Any]] = {}
    plan_by_index: dict[int, str] = {}
    plan_index_by_id: dict[str, int] = {}
    for ordinal, row in enumerate(plan):
        candidate_id = row.get("candidate_id")
        index = row.get("input_index", ordinal)
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("Candidate plan has a missing candidate_id.")
        if type(index) is not int or index != ordinal:
            raise ValueError("Candidate plan order must match its global input_index.")
        if candidate_id in plan_by_id or index in plan_by_index:
            raise ValueError("Candidate plan contains duplicate IDs or global input indices.")
        xy = row.get("position_xy_m")
        if not isinstance(xy, list) or len(xy) != 2:
            raise ValueError("Candidate plan row has invalid position_xy_m.")
        _number(xy[0], "candidate east_m")
        _number(xy[1], "candidate north_m")
        plan_by_id[candidate_id] = row
        plan_by_index[index] = candidate_id
        plan_index_by_id[candidate_id] = index

    if set(inventory_ids) != set(plan_by_id):
        raise ValueError("Candidate inventory IDs differ from the frozen candidate plan.")
    domain_count = identity.get("candidate_domain_count")
    if type(domain_count) is not int or domain_count < 0 or len(plan_by_id) != domain_count:
        raise ValueError("Candidate plan count differs from the identity's complete domain count.")
    chunks, hypothesis_rows = _read_chunks(output_root)
    if set(plan_by_index) and set(plan_by_index) != set(range(len(plan_by_index))):
        raise ValueError("Candidate plan input_index values are not contiguous from zero.")
    candidate_rows: dict[str, dict[str, Any]] = {}
    candidate_chunk: dict[str, int] = {}
    committed_indices: set[int] = set()
    allowed_actions = {"exact_scored", "exact_invalid_water", "exact_unresolved", "pruned"}
    for chunk in chunks:
        if not isinstance(chunk.get("candidates"), list):
            raise ValueError("Chunk is missing its candidate audit rows.")
        chunk_candidate_count = chunk.get("candidate_count")
        if type(chunk_candidate_count) is not int or chunk_candidate_count != len(
            chunk["candidates"]
        ):
            raise ValueError("Chunk candidate_count differs from its audit row count.")
        for row in chunk["candidates"]:
            action = row.get("action")
            if action not in allowed_actions:
                raise ValueError(f"Unsupported or missing candidate action: {action!r}.")
            candidate_id, index = row.get("candidate_id"), row.get("input_index")
            if candidate_id not in plan_by_id or type(index) is not int:
                raise ValueError("Chunk candidate does not map to the frozen candidate plan.")
            if plan_index_by_id[candidate_id] != index:
                raise ValueError("Chunk candidate global input_index differs from the plan.")
            if candidate_id in candidate_rows or index in committed_indices:
                raise ValueError("Candidate was committed more than once across attempts.")
            candidate_rows[candidate_id] = row
            candidate_chunk[candidate_id] = chunk["chunk_index"]
            committed_indices.add(index)

    saved: list[SavedCandidate] = []
    hyp_by_index: dict[int, Hypothesis] = {}
    for row in hypothesis_rows:
        index = row.get("input_index")
        if type(index) is not int or index in hyp_by_index:
            raise ValueError("Hypothesis sidecar has invalid or duplicate input_index.")
        hypothesis = decode_hypothesis(row.get("hypothesis"))
        candidate_id = plan_by_index.get(index)
        if candidate_id is None or candidate_id not in candidate_rows:
            raise ValueError("Hypothesis sidecar refers to a candidate absent from durable chunks.")
        audit = candidate_rows[candidate_id]
        if audit.get("action") != "exact_scored":
            raise ValueError("Hypothesis sidecar candidate is not marked exact_scored.")
        position = plan_by_id[candidate_id]["position_xy_m"]
        if (hypothesis.east_m, hypothesis.north_m) != tuple(map(float, position)):
            raise ValueError("Saved hypothesis coordinates differ from the frozen plan.")
        if (
            audit.get("score") is not None
            and _number(audit["score"], "candidate score") != hypothesis.score
        ):
            raise ValueError("Chunk score differs from hypothesis sidecar.")
        hyp_by_index[index] = hypothesis
        saved.append(SavedCandidate(index, candidate_id, "exact_scored", hypothesis))

    exact_indices = {
        row["input_index"] for row in candidate_rows.values() if row.get("action") == "exact_scored"
    }
    if exact_indices != set(hyp_by_index):
        raise ValueError("Exact-scored candidates and saved hypothesis sidecars do not match.")
    saved.sort(key=lambda item: (item.hypothesis.score, item.input_index, item.candidate_id))
    for previous, current in zip(saved, saved[1:]):
        if current.hypothesis.score < previous.hypothesis.score:
            raise ValueError("Internal deterministic score ordering failed.")
    status_counts = Counter(
        str(row.get("action") or "missing_action") for row in candidate_rows.values()
    )
    disposition_counts: dict[str, int] = {}
    dispositions_path = output_root / "candidate-dispositions.jsonl"
    if not dispositions_path.is_file():
        raise ValueError("Candidate disposition ledger is required for full outcome accounting.")
    disposition_rows = read_jsonl(dispositions_path)
    disposition_ids = [row.get("candidate_id") for row in disposition_rows]
    if len(disposition_ids) != len(set(disposition_ids)):
        raise ValueError("Candidate dispositions contain duplicate IDs.")
    if plan_by_id and set(disposition_ids) != set(plan_by_id):
        raise ValueError("Candidate dispositions do not cover the complete frozen plan.")
    disposition_by_id = {row["candidate_id"]: row for row in disposition_rows}
    allowed_dispositions = {
        "exact_scored",
        "exact_invalid_water",
        "exact_unresolved",
        "pruned",
        "worker_reported_failure",
        "not_dispatched",
        "worker_state_unknown_at_interruption",
        "unvisited_after_worker_exit",
        "not_selected_by_explicit_prefix_profile",
        "prefix_ordering_not_reached_before_interruption",
    }
    for candidate_id, disposition in disposition_by_id.items():
        status = disposition.get("status")
        if status not in allowed_dispositions:
            raise ValueError(f"Unsupported candidate disposition: {status!r}.")
        chunk_row = candidate_rows.get(candidate_id)
        if chunk_row is not None and status != chunk_row.get("action"):
            raise ValueError("Candidate disposition differs from its durable chunk action.")
        if chunk_row is None and status in {
            "exact_scored",
            "exact_invalid_water",
            "exact_unresolved",
            "pruned",
        }:
            raise ValueError("Committed candidate disposition has no durable chunk row.")
    disposition_counts = dict(Counter(str(row.get("status")) for row in disposition_rows))
    totals = manifest.get("totals", {})
    if totals.get("completed_candidate_count") is not None and totals.get(
        "completed_candidate_count"
    ) != len(candidate_rows):
        raise ValueError("Manifest completed-candidate count differs from durable chunks.")
    declared_dispositions = manifest.get("candidate_disposition_counts")
    if declared_dispositions is not None and declared_dispositions != disposition_counts:
        raise ValueError("Disposition ledger counts differ from the run manifest.")
    complete = bool(totals.get("all_candidates_accounted")) and bool(
        totals.get("selected_request_complete")
    )
    if manifest.get("status") in ("complete_request_timeout", "external_timeout"):
        complete = False
    return LoadedRun(
        manifest=manifest,
        identity=identity,
        candidates=tuple(saved),
        disposition_counts=disposition_counts,
        status_counts=dict(status_counts),
        complete=complete,
    )


def geodesic_error_m(
    candidate_xy: tuple[float, float],
    truth_xy: tuple[float, float],
    *,
    candidate_crs: str,
    truth_crs: str,
) -> float:
    candidate_lon, candidate_lat = Transformer.from_crs(
        candidate_crs, "EPSG:4326", always_xy=True
    ).transform(*candidate_xy)
    truth_lon, truth_lat = Transformer.from_crs(truth_crs, "EPSG:4326", always_xy=True).transform(
        *truth_xy
    )
    if not all(
        math.isfinite(value) for value in (candidate_lon, candidate_lat, truth_lon, truth_lat)
    ):
        raise ValueError("Coordinate transformation returned nonfinite geographic coordinates.")
    return abs(_GEOD.inv(truth_lon, truth_lat, candidate_lon, candidate_lat)[2])


def evaluate_candidates(
    run: LoadedRun,
    *,
    truth_xy: tuple[float, float],
    truth_crs: str,
    search_crs: str,
    truth_ids_by_report: Mapping[int, set[str]],
) -> dict[str, Any]:
    """Summarize score rank and evaluator-only error rank deterministically."""
    rows = []
    for rank, item in enumerate(run.candidates, 1):
        hyp = item.hypothesis
        direct_correct = sum(
            assignment.catalog_key in truth_ids_by_report.get(assignment.report_index, set())
            for assignment in hyp.assignments
        )
        rows.append(
            {
                "score_rank": rank,
                "input_index": item.input_index,
                "candidate_id": item.candidate_id,
                "score": hyp.score,
                "position_xy_m": [hyp.east_m, hyp.north_m],
                "geodesic_error_m": geodesic_error_m(
                    (hyp.east_m, hyp.north_m),
                    truth_xy,
                    candidate_crs=search_crs,
                    truth_crs=truth_crs,
                ),
                "assignment_count": len(hyp.assignments),
                "direct_truth_key_assignment_count": direct_correct,
                "assignments": [
                    {
                        "report_index": assignment.report_index,
                        "catalog_key": assignment.catalog_key,
                        "residual_deg": assignment.residual_deg,
                        "cost": assignment.cost,
                    }
                    for assignment in hyp.assignments
                ],
                "score_components": (
                    {
                        "matched_cost": hyp.score_components.matched_cost,
                        "clutter_cost": hyp.score_components.clutter_cost,
                        "missed_cost": hyp.score_components.missed_cost,
                        "total_cost": hyp.score_components.total_cost,
                    }
                    if hyp.score_components is not None
                    else None
                ),
            }
        )
    by_error = sorted(
        rows, key=lambda row: (row["geodesic_error_m"], row["score"], row["input_index"])
    )
    return {
        "schema": "a35-cached-finite-domain-evaluation-v1",
        "evaluation_only": True,
        "truth_used_for_search": False,
        "run_complete": run.complete,
        "global_certificate_claim": False,
        "run_id": run.manifest.get("run_id"),
        "run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "execution_mode": run.manifest.get("execution_mode"),
        "candidate_domain_count": run.identity.get("candidate_domain_count"),
        "saved_exact_hypothesis_count": len(rows),
        "all_candidates_exact_scored": (
            run.complete
            and run.status_counts.get("exact_scored", 0)
            == run.identity.get("candidate_domain_count")
        ),
        "minimum_error_scope": (
            "all exact-scored hypotheses saved in this attempt; best-so-far if incomplete"
        ),
        "status_counts": run.status_counts,
        "disposition_counts": run.disposition_counts,
        "run_totals": run.manifest.get("totals"),
        "pool_error": run.manifest.get("pool_error"),
        "supervisor_status": run.manifest.get("status"),
        "top_score_ranked": rows[:20],
        "minimum_error_ranked": by_error[:20],
        "best_score_candidate": rows[0] if rows else None,
        "minimum_error_candidate_evaluator_only": by_error[0] if by_error else None,
        "score_error_points": [
            {
                "score": row["score"],
                "error_m": row["geodesic_error_m"],
                "input_index": row["input_index"],
            }
            for row in rows
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--static-input-dir", type=Path, required=True)
    parser.add_argument("--truth-dir", type=Path, required=True)
    parser.add_argument("--expected-bindings", type=Path, required=True)
    parser.add_argument("--search-crs", default="EPSG:32618")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = read_json(args.expected_bindings)
    if not isinstance(expected, dict):
        raise ValueError("Expected binding file must contain a JSON object.")
    actual_static_hashes = {
        name: sha256_file(args.static_input_dir / filename)
        for name, filename in {
            "measurements": "measurements.json",
            "catalog": "catalog.json",
            "map": "map.json",
        }.items()
    }
    if expected.get("static_input_sha256") != actual_static_hashes:
        raise ValueError("Frozen static inputs do not match the caller-pinned hashes.")
    if args.search_crs != "EPSG:32618":
        raise ValueError("This frozen trial requires search coordinates in EPSG:32618.")
    run = load_run(args.output_root, expected_bindings=expected)

    # Truth files are opened only after immutable search output has been verified.
    truth_path = args.truth_dir / "truth.json"
    support_path = args.truth_dir / "support.json"
    truth = read_json(truth_path)
    support = read_json(support_path)
    truth_crs_doc = read_json(args.truth_dir / "truth-crs.json")
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    truth_contract = read_contract(truth_path, EvaluationTruth)
    if truth_contract.trial_id != _EXPECTED_TRIAL_ID or truth.get("trial_id") != _EXPECTED_TRIAL_ID:
        raise ValueError("Truth does not identify the frozen development trial.")
    if not isinstance(support, dict) or support.get("supported") is not True:
        raise ValueError("Frozen evaluator support record does not mark this trial supported.")
    if support.get("valid_reference_water") is not True:
        raise ValueError("Frozen evaluator support record does not validate reference water.")
    if (
        support.get("port_id") != "port-001"
        or support.get("placement_id") != "placement-001"
        or type(support.get("detected_physical_catalog_matched_groups")) is not int
    ):
        raise ValueError("Frozen evaluator support record does not match the selected trial.")
    truth_xy = (truth_contract.observer_east_m, truth_contract.observer_north_m)
    if truth_crs_doc.get("trial_id") not in (None, truth.get("trial_id")):
        raise ValueError("Truth CRS metadata belongs to a different trial.")
    truth_crs = truth_crs_doc.get("coordinate_reference_system")
    if truth_crs != "EPSG:32619":
        raise ValueError("This frozen trial requires truth coordinates in EPSG:32619.")
    true_ids: dict[int, set[str]] = {}
    for obj in truth.get("objects", []):
        if obj.get("observation_index") is not None:
            key = str(obj.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                true_ids.setdefault(int(obj["observation_index"]), set()).add(key)
    result = evaluate_candidates(
        run,
        truth_xy=truth_xy,
        truth_crs=truth_crs,
        search_crs=args.search_crs,
        truth_ids_by_report=true_ids,
    )
    result["evaluator_support"] = {
        "supported": support["supported"],
        "valid_reference_water": support["valid_reference_water"],
        "detected_physical_catalog_matched_groups": support[
            "detected_physical_catalog_matched_groups"
        ],
    }
    result["input_sha256"] = {
        "measurements": sha256_file(args.static_input_dir / "measurements.json"),
        "catalog": sha256_file(args.static_input_dir / "catalog.json"),
        "map": sha256_file(args.static_input_dir / "map.json"),
        "truth": sha256_file(args.truth_dir / "truth.json"),
        "truth_support": sha256_file(args.truth_dir / "support.json"),
        "truth_crs": sha256_file(args.truth_dir / "truth-crs.json"),
        "run_manifest": sha256_file(args.output_root / "run-manifest.json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
