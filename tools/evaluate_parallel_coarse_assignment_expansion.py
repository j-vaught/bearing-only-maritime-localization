"""Independent evaluator for the coarse-seed parallel assignment expansion run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from tools import evaluate_parallel_assignment_expansion as parallel_eval
from tools import run_assignment_expansion_diagnostic as serial

COARSE_LABEL = "a20.cached_hypothesis"
ASSIGNED_LABELS = ("cached_hypothesis", "refined_mode.hypothesis")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise parallel_eval.EvaluationError(message)


def verify_coarse_selection(
    rows: list[dict[str, Any]],
    a20_rows: list[dict[str, Any]],
    assigned_rows: list[dict[str, Any]],
) -> None:
    """Prove mode identities, original A20 seeds, and all three serialized starts."""
    require(len(a20_rows) == len(assigned_rows) == 64, "source bundles must each contain 64 modes")
    require(len(rows) <= 64, "coarse output has more than 64 rows")
    for index, row in enumerate(rows):
        a20 = a20_rows[index]
        assigned = assigned_rows[index]
        require(row.get("mode_index") == index, f"row {index} mode order mismatch")
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
    manifest, rows, events = parallel_eval.validate_manifest(output)
    require(
        manifest.get("schema") == "parallel-assignment-expansion-diagnostic-v1",
        "unexpected parallel diagnostic schema",
    )
    require(manifest.get("seed_stage") == "coarse", "artifact is not the coarse seed stage")
    require(
        manifest.get("seed_source") == "A20.cached_hypothesis",
        "manifest coarse source does not identify original A20 hypotheses",
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
    parallel_eval.validate_event_inventory(events, rows)
    checkpoint = parallel_eval.read_json(output / "checkpoint.json")
    require(checkpoint.get("status") == manifest.get("status"), "checkpoint status differs")
    require(
        checkpoint.get("rows_sha256") == manifest.get("rows_sha256"), "checkpoint rows hash differs"
    )
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
    for index, row in enumerate(rows):
        for item in row["start_alternatives"]:
            raw = serial._hypothesis(item["saved_hypothesis"], "saved start")
            exact = serial._revalidate(scorer, raw)
            require(
                serial.asdict(exact) == item["canonical_hypothesis"],
                f"mode {index} canonical start rescore mismatch",
            )
        for key in ("seed", "best_hypothesis"):
            source = serial._hypothesis(row[key], key)
            exact = serial._revalidate(scorer, source)
            require(serial.asdict(exact) == row[key], f"mode {index} {key} rescore mismatch")


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
        "all_three_starts_rescored": True,
        "final_hypotheses_rescored": True,
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
