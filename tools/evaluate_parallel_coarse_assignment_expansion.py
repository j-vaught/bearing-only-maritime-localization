"""Independent evaluator for the coarse-seed parallel assignment expansion run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from tools import evaluate_parallel_assignment_expansion as parallel_eval
from tools import run_assignment_expansion_diagnostic as serial

COARSE_LABEL = "a20.cached_hypothesis"
ASSIGNED_LABELS = ("cached_hypothesis", "refined_mode.hypothesis")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise parallel_eval.EvaluationError(message)


def _row_mapping(value: Any) -> dict[str, Any]:
    if type(value) is dict:
        return cast(dict[str, Any], value)
    require(is_dataclass(value), "source mode row is not a mapping or dataclass")
    converted = asdict(value)
    require(type(converted) is dict, "source mode row dataclass did not serialize to object")
    return converted


def verify_coarse_selection(
    rows: list[dict[str, Any]],
    a20_rows: Sequence[Any],
    assigned_rows: Sequence[Any],
) -> None:
    """Prove mode identities, original A20 seeds, and all three serialized starts."""
    require(len(a20_rows) == len(assigned_rows) == 64, "source bundles must each contain 64 modes")
    require(len(rows) <= 64, "coarse output has more than 64 rows")
    seen_modes: set[int] = set()
    for row in rows:
        mode = row.get("mode_index")
        require(type(mode) is int and 0 <= mode < 64, "row mode index is invalid")
        mode = cast(int, mode)
        require(mode not in seen_modes, f"duplicate row for mode {mode}")
        seen_modes.add(mode)
        index = mode
        a20 = _row_mapping(a20_rows[index])
        assigned = _row_mapping(assigned_rows[index])
        require(
            row.get("input_index") == a20.get("input_index") == assigned.get("input_index"),
            f"row {index} input_index is not the frozen A20/assigned input",
        )
        require(
            row.get("candidate_id") == a20.get("candidate_id") == assigned.get("candidate_id"),
            f"row {index} candidate identity differs from A20/assigned source",
        )
        require(row.get("seed_stage") == "coarse", f"row {index} is not coarse-stage")
        require(row.get("selected_start_label") == COARSE_LABEL, f"row {index} did not select A20")
        require(
            row.get("selected_start_source") == "A20.cached_hypothesis",
            f"row {index} coarse source label mismatch",
        )
        alternatives_raw = row.get("start_alternatives")
        require(
            type(alternatives_raw) is list and len(alternatives_raw) == 3,
            f"row {index} must contain exactly three start alternatives",
        )
        alternatives = cast(list[dict[str, Any]], alternatives_raw)
        by_label = {item.get("label"): item for item in alternatives}
        require(len(by_label) == 3, f"row {index} has duplicate start labels")
        require(
            set(by_label) == set(ASSIGNED_LABELS) | {COARSE_LABEL},
            f"row {index} start labels differ from frozen three-start set",
        )
        a20_saved = a20.get("cached_hypothesis")
        assigned_starts = {
            str(item["label"]): item.get("final_hypothesis") for item in assigned.get("starts", [])
        }
        require(
            by_label[COARSE_LABEL].get("saved_hypothesis") == a20_saved,
            f"row {index} coarse seed differs from original A20 hypothesis",
        )
        for label in ASSIGNED_LABELS:
            require(
                by_label[label].get("saved_hypothesis") == assigned_starts.get(label),
                f"row {index} saved assigned alternative differs for {label}",
            )
        require(
            by_label[COARSE_LABEL].get("origin") == "A20.cached_hypothesis",
            f"row {index} coarse source origin is missing",
        )
        require(
            sum(item.get("selected") is True for item in alternatives) == 1,
            f"row {index} must select exactly one start",
        )
        require(
            by_label[COARSE_LABEL].get("selected") is True,
            f"row {index} did not mark original A20 start selected",
        )
        for item in alternatives:
            require(
                item.get("canonical_match") is True,
                f"row {index} has an unverified serialized canonical start",
            )
            require(
                type(item.get("canonical_hypothesis")) is dict,
                f"row {index} canonical alternative is missing",
            )
        require(
            row.get("seed") == by_label[COARSE_LABEL].get("canonical_hypothesis"),
            f"row {index} expansion seed differs from canonical A20 start",
        )
        require(
            type(row.get("best_hypothesis")) is dict,
            f"row {index} final best hypothesis is missing",
        )
        score = row["best_hypothesis"].get("score")
        require(
            type(score) in (int, float) and math.isfinite(float(score)),
            f"row {index} final score is not finite",
        )


def validate_coarse_artifact(
    output: Path, a20_root: Path, assigned_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = parallel_eval.read_json(output / "manifest.json")
    require(manifest.get("mode_count") == 64, "artifact mode count is not 64")
    rows_file = manifest.get("rows_file")
    require(
        type(rows_file) is str and Path(cast(str, rows_file)).name == rows_file,
        "rows_file is not a safe basename",
    )
    rows_path = output / cast(str, rows_file)
    require(
        parallel_eval.sha256_file(rows_path) == manifest.get("rows_sha256"),
        "rows hash differs from manifest",
    )
    rows = parallel_eval.read_jsonl(rows_path)
    row_modes_raw = [row.get("mode_index") for row in rows]
    require(
        all(type(mode) is int and 0 <= mode < 64 for mode in row_modes_raw),
        "row mode index is invalid",
    )
    row_modes = cast(list[int], row_modes_raw)
    require(len(set(row_modes)) == len(row_modes), "duplicate row mode index")
    completed_raw = manifest.get("completed_mode_indices")
    require(type(completed_raw) is list, "completed mode list is missing")
    completed = cast(list[int], completed_raw)
    require(sorted(completed) == sorted(row_modes), "completed mode list differs from rows")
    require(manifest.get("completed_mode_count") == len(rows), "completed mode count differs")
    failed_raw = manifest.get("failed_mode_indices")
    unknown_raw = manifest.get("unknown_mode_indices")
    require(type(failed_raw) is list and type(unknown_raw) is list, "mode dispositions are missing")
    failed = cast(list[int], failed_raw)
    unknown = cast(list[int], unknown_raw)
    require(
        sorted(completed + failed + unknown) == list(range(64)),
        "mode dispositions do not partition the fixed inventory",
    )
    require(manifest.get("failed_mode_count") == len(failed), "failed mode count differs")
    require(manifest.get("unknown_mode_count") == len(unknown), "unknown mode count differs")
    events_file = manifest.get("events_file")
    require(
        type(events_file) is str and Path(cast(str, events_file)).name == events_file,
        "events_file is not a safe basename",
    )
    events = parallel_eval.verify_event_chain(output / cast(str, events_file), manifest)
    checkpoint_file = manifest.get("checkpoint_file")
    require(
        type(checkpoint_file) is str and Path(cast(str, checkpoint_file)).name == checkpoint_file,
        "checkpoint_file is not a safe basename",
    )
    checkpoint = parallel_eval.read_json(output / cast(str, checkpoint_file))
    require(
        checkpoint.get("rows_sha256") == manifest.get("rows_sha256"), "checkpoint rows hash differs"
    )
    parallel_eval.verify_checkpoint_prefix(output, checkpoint, events)
    parallel_eval.event_index(events)
    allowed_event_modes = set(completed) | set(failed) | set(unknown)
    require(
        all(event.get("mode_index") in allowed_event_modes for event in events),
        "event references an unrecorded mode",
    )
    for row in rows:
        mode = row["mode_index"]
        result_count = sum(
            1
            for event in events
            if event.get("mode_index") == mode and event.get("phase") == "branch_result"
        )
        started_count = sum(
            1
            for event in events
            if event.get("mode_index") == mode and event.get("phase") == "branch_started"
        )
        require(
            result_count == started_count == row.get("branch_event_count"),
            f"mode {mode} branch event inventory differs from row",
        )
    require(checkpoint.get("status") == manifest.get("status"), "checkpoint status differs")
    require(
        manifest.get("schema") == "parallel-assignment-expansion-diagnostic-v1",
        "unexpected parallel diagnostic schema",
    )
    require(manifest.get("seed_stage") == "coarse", "artifact is not the coarse seed stage")
    require(
        manifest.get("seed_selection_rule")
        == "original A20 cached_hypothesis for the matching assigned mode",
        "manifest coarse selection rule does not identify the original A20 source",
    )
    require(manifest.get("full_domain_complete") is False, "diagnostic claims full-domain coverage")
    require(manifest.get("decision_status") == "ambiguous", "diagnostic claims a decision")
    require(manifest.get("certificate_scope") == "none", "diagnostic claims a certificate")
    require(
        manifest.get("truth_used") is False and manifest.get("evaluator_used") is False,
        "producer claims evaluator/truth usage",
    )
    require(manifest.get("a20_root") == str(a20_root.resolve()), "A20 root differs")
    require(manifest.get("assigned_root") == str(assigned_root.resolve()), "assigned root differs")
    require(
        parallel_eval.sha256_file(a20_root / "manifest.json")
        == manifest.get("a20_manifest_sha256"),
        "A20 manifest binding mismatch",
    )
    require(
        parallel_eval.sha256_file(assigned_root / "manifest.json")
        == manifest.get("assigned_manifest_sha256"),
        "assigned manifest binding mismatch",
    )
    assigned_rows, assigned_manifest = serial.load_assigned_bundle(assigned_root, a20_root)
    a20_rows, a20_manifest = serial.load_a20_bundle(a20_root)
    require(
        manifest.get("a20_rows_sha256") == a20_manifest.get("rows_sha256"),
        "A20 rows binding mismatch",
    )
    require(
        manifest.get("input_assigned_package_commit") == assigned_manifest.get("package_commit"),
        "assigned package pin mismatch",
    )
    require(
        manifest.get("source_pins") == assigned_manifest.get("source_pins"),
        "source pins differ from assigned bundle",
    )
    require(
        manifest.get("frozen_scoring_source_sha")
        == manifest["source_pins"].get("frozen_scoring_source_sha"),
        "frozen scoring source pin mismatch",
    )
    require(
        manifest.get("seed_stage_identity")
        == hashlib.sha256(
            parallel_eval.canonical_bytes(
                {"source_pins": manifest["source_pins"], "seed_stage": "coarse"}
            )
        ).hexdigest(),
        "seed-stage identity hash mismatch",
    )
    verify_coarse_selection(rows, cast(list[dict[str, Any]], a20_rows), assigned_rows)
    return manifest, rows, events, cast(list[dict[str, Any]], a20_rows)


def canonical_rescore(
    rows: list[dict[str, Any]],
    a20_rows: list[dict[str, Any]],
    a20_root: Path,
    assigned_root: Path,
) -> None:
    """Recompute every saved alternative and final best under the pinned input scorer."""
    assigned_rows, assigned_manifest = serial.load_assigned_bundle(assigned_root, a20_root)
    a20_manifest = serial.read_json(a20_root / "manifest.json")
    source_pins, _ = serial.verify_source_pins(
        a20_manifest,
        a20_root,
        Path(assigned_manifest["source_pins"]["attempt"]),
        Path(assigned_manifest["source_pins"]["input_paths"]["measurements"]).parent,
        Path(assigned_manifest["source_pins"]["config_path"]),
    )
    static = Path(source_pins["input_paths"]["measurements"]).parent
    scorer = serial.build_scorer(static, Path(source_pins["config_path"]))
    for row in rows:
        mode = row["mode_index"]
        for item in row["start_alternatives"]:
            raw = serial._hypothesis(item["saved_hypothesis"], "saved start")
            exact = serial._revalidate(scorer, raw)
            require(
                serial.asdict(exact) == item["canonical_hypothesis"],
                f"mode {mode} canonical start rescore mismatch",
            )
        for key in ("seed", "best_hypothesis"):
            source = serial._hypothesis(row[key], key)
            exact = serial._revalidate(scorer, source)
            require(serial.asdict(exact) == row[key], f"mode {mode} {key} rescore mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--a20", type=Path, required=True)
    parser.add_argument("--assigned", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    manifest, rows, events, a20_rows = validate_coarse_artifact(
        args.output, args.a20, args.assigned
    )
    canonical_rescore(rows, a20_rows, args.a20, args.assigned)
    result = {
        "schema": "parallel-coarse-assignment-expansion-evaluation-v1",
        "evidence_status": "search_integrity_verified",
        "accuracy_status": "not_evaluated",
        "producer_status": manifest.get("status"),
        "mode_count": 64,
        "completed_mode_count": len(rows),
        "failed_mode_count": manifest.get("failed_mode_count"),
        "unknown_mode_count": manifest.get("unknown_mode_count"),
        "event_count": len(events),
        "manifest_sha256": parallel_eval.sha256_file(args.output / "manifest.json"),
        "rows_sha256": manifest.get("rows_sha256"),
        "events_sha256": manifest.get("events_sha256"),
        "coarse_seed_verified": True,
        "all_three_starts_rescored": bool(rows)
        and all(len(row.get("start_alternatives", [])) == 3 for row in rows),
        "final_hypotheses_rescored": bool(rows),
        "truth_used_for_search_or_proposal_selection": False,
        "certificate_scope": "none",
        "mode_rows": [
            {
                "mode_index": row["mode_index"],
                "input_index": row["input_index"],
                "candidate_id": row["candidate_id"],
                "selected_start_label": row["selected_start_label"],
                "seed_score": row["seed"]["score"],
                "best_score": row["best_hypothesis"]["score"],
                "status": row.get("status"),
                "completed": row.get("completed"),
                "budget_exhausted": row.get("budget_exhausted"),
                "counters": row.get("counters"),
            }
            for row in rows
        ],
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()


def validate_preparation_failure(output: Path) -> dict[str, Any]:
    """Verify an eventless pre-dispatch failure without treating it as accuracy data."""
    manifest = parallel_eval.read_json(output / "manifest.json")
    checkpoint = parallel_eval.read_json(output / str(manifest.get("checkpoint_file")))
    require(
        manifest.get("schema") == "parallel-assignment-expansion-diagnostic-v1",
        "unexpected failed-run schema",
    )
    require(manifest.get("seed_stage") == "coarse", "failed run is not coarse-stage")
    require(manifest.get("status") == "failed", "artifact is not a failed attempt")
    require(
        manifest.get("completed_mode_count") == 0 and manifest.get("failed_mode_count") == 0,
        "preparation failure unexpectedly has completed/failed modes",
    )
    require(
        manifest.get("unknown_mode_count") == 64
        and manifest.get("unknown_mode_indices") == list(range(64)),
        "preparation failure does not retain 64 unknown modes",
    )
    require(
        manifest.get("error") == checkpoint.get("error"),
        "failure reason differs between manifest and checkpoint",
    )
    rows_path = output / str(manifest.get("rows_file"))
    events_path = output / str(manifest.get("events_file"))
    require(
        parallel_eval.sha256_file(rows_path)
        == manifest.get("rows_sha256")
        == checkpoint.get("rows_sha256"),
        "empty rows hash mismatch",
    )
    require(
        parallel_eval.sha256_file(events_path)
        == manifest.get("events_sha256")
        == checkpoint.get("events_sha256"),
        "empty events hash mismatch",
    )
    require(
        rows_path.stat().st_size == 0 and events_path.stat().st_size == 0,
        "preparation failure unexpectedly contains row/event data",
    )
    require(
        manifest.get("event_count") == checkpoint.get("event_count") == 0,
        "preparation failure unexpectedly records events",
    )
    require(
        manifest.get("event_digest") == checkpoint.get("event_digest") == "",
        "empty event digest mismatch",
    )
    require(
        checkpoint.get("status") == "failed" and checkpoint.get("resume_supported") is False,
        "checkpoint does not identify a terminal non-resumable failure",
    )
    return {
        "schema": "parallel-coarse-assignment-expansion-evaluation-v1",
        "evidence_status": "pre_dispatch_failure_verified",
        "accuracy_status": "not_evaluated",
        "producer_status": "failed",
        "error": manifest.get("error"),
        "mode_count": 64,
        "completed_mode_count": 0,
        "failed_mode_count": 0,
        "unknown_mode_count": 64,
        "event_count": 0,
        "manifest_sha256": parallel_eval.sha256_file(output / "manifest.json"),
        "checkpoint_sha256": parallel_eval.sha256_file(
            output / str(manifest.get("checkpoint_file"))
        ),
        "rows_sha256": manifest.get("rows_sha256"),
        "events_sha256": manifest.get("events_sha256"),
        "truth_read": False,
        "certificate_scope": "none",
    }
