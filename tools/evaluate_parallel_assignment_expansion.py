"""Independent source-bound evaluator for serial and parallel expansion artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

MODE_COUNT = 64
ROWS_NAME = "assignment-expansion-rows.jsonl"
EVENTS_NAME = "branch-events.jsonl"
CHECKPOINT_NAME = "checkpoint.json"
TIMING_KEYS = {"elapsed_s", "elapsed_seconds", "validation_and_setup_elapsed_s"}
BRANCH_PHASES = {"branch_started", "branch_result"}
TERMINAL_PHASES = {"mode_result", "mode_failure", "failure"}


class EvaluationError(ValueError):
    """Artifact failed an independent consistency check."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(type(value) is dict, f"{path.name} must contain an object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{path.name}:{line_number} is not valid JSON") from exc
        require(type(value) is dict, f"{path.name}:{line_number} must be an object")
        rows.append(value)
    return rows


def strip_timing(value: Any, *, top_level: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_timing(item, top_level=True)
            for key, item in value.items()
            if not (top_level and key in TIMING_KEYS)
        }
    if isinstance(value, list):
        return [strip_timing(item) for item in value]
    return value


def verify_event_chain(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    require(path.is_file(), f"missing event file {path}")
    require(
        sha256_file(path) == manifest.get("events_sha256"),
        f"{path.name} hash differs from manifest",
    )
    records = read_jsonl(path)
    previous = ""
    mode_event_counts: dict[int, int] = {}
    for index, record in enumerate(records):
        require(record.get("event_index") == index, f"{path.name} event indices are not contiguous")
        require(
            record.get("previous_event_sha256") == previous,
            f"{path.name} event hash chain is broken at {index}",
        )
        digest = canonical_hash(
            {key: value for key, value in record.items() if key != "event_sha256"}
        )
        require(record.get("event_sha256") == digest, f"{path.name} event hash mismatch at {index}")
        mode = record.get("mode_index")
        require(type(mode) is int and 0 <= mode < MODE_COUNT, "event mode_index is invalid")
        mode_index = cast(int, mode)
        if "mode_event_index" in record:
            mode_event_index = record.get("mode_event_index")
            require(
                mode_event_index == mode_event_counts.get(mode_index, 0),
                f"{path.name} mode event indices are not contiguous for mode {mode_index}",
            )
            mode_event_counts[mode_index] = mode_event_counts.get(mode_index, 0) + 1
        require(record.get("phase") in BRANCH_PHASES | TERMINAL_PHASES, "unknown event phase")
        require(type(record.get("event")) is dict, "event payload must be an object")
        previous = digest
    require(
        manifest.get("event_count") == len(records), "manifest event_count differs from event file"
    )
    require(
        manifest.get("event_digest") == previous, "manifest event_digest differs from event chain"
    )
    return records


def event_index(records: list[dict[str, Any]]) -> dict[tuple[int, str, int], dict[str, Any]]:
    result: dict[tuple[int, str, int], dict[str, Any]] = {}
    per_mode: dict[int, list[tuple[str, int]]] = {}
    for record in records:
        phase = record["phase"]
        if phase == "failure":
            require(record["event"].get("status") == "mode_exception", "malformed failure event")
            continue
        if phase not in BRANCH_PHASES:
            continue
        event = record["event"]
        branch_id = event.get("branch_id")
        require(type(branch_id) is int and branch_id >= 0, "branch_id must be nonnegative")
        key = (record["mode_index"], phase, branch_id)
        require(key not in result, f"duplicate branch event {key}")
        result[key] = event
        per_mode.setdefault(record["mode_index"], []).append((phase, branch_id))
    for mode, sequence in per_mode.items():
        require(
            sequence
            == [
                pair
                for branch_id in range(max((item[1] for item in sequence), default=-1) + 1)
                for pair in (("branch_started", branch_id), ("branch_result", branch_id))
                if (
                    ("branch_started", branch_id) in sequence
                    or ("branch_result", branch_id) in sequence
                )
            ][: len(sequence)],
            f"mode {mode} branch events are not ordered start/result pairs",
        )
        started = {branch for phase, branch in sequence if phase == "branch_started"}
        finished = {branch for phase, branch in sequence if phase == "branch_result"}
        require(finished <= started, f"mode {mode} has a result without a start")
    return result


def validate_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows_path = root / ROWS_NAME
    require(rows_path.is_file(), f"missing rows file {rows_path}")
    require(
        sha256_file(rows_path) == manifest.get("rows_sha256"), "rows hash differs from manifest"
    )
    rows = read_jsonl(rows_path)
    ids = [row.get("mode_index") for row in rows]
    require(ids == list(range(len(rows))), "rows do not form a deterministic mode prefix")
    require(
        manifest.get("completed_mode_count") == len(rows),
        "completed row count differs from manifest",
    )
    for row in rows:
        require(type(row.get("best_hypothesis")) is dict, "row best hypothesis is missing")
        require(type(row.get("seed")) is dict, "row seed is missing")
        require(type(row.get("counters")) is dict, "row counters are missing")
        require(
            row.get("branch_event_count") == row["counters"].get("visited_branches", 0),
            "row branch inventory mismatch",
        )
    return rows


def verify_checkpoint_prefix(
    root: Path, checkpoint: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    count = checkpoint.get("event_count")
    require(type(count) is int and 0 <= count <= len(records), "checkpoint event prefix is invalid")
    raw_lines = (root / EVENTS_NAME).read_bytes().splitlines(keepends=True)
    require(len(raw_lines) == len(records), "event lines differ from parsed records")
    prefix_bytes = b"".join(raw_lines[:count])
    require(
        hashlib.sha256(prefix_bytes).hexdigest() == checkpoint.get("events_sha256"),
        "checkpoint is not an exact event prefix",
    )
    previous = ""
    for index, record in enumerate(records[:count]):
        require(record.get("event_index") == index, "checkpoint prefix event index is invalid")
        require(
            record.get("previous_event_sha256") == previous, "checkpoint prefix chain is broken"
        )
        previous = canonical_hash(
            {key: value for key, value in record.items() if key != "event_sha256"}
        )
        require(record.get("event_sha256") == previous, "checkpoint prefix hash is invalid")
    require(previous == checkpoint.get("event_digest"), "checkpoint prefix digest differs")


def validate_manifest(
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = read_json(root / "manifest.json")
    require(manifest.get("mode_count") == MODE_COUNT, "artifact mode count is not 64")
    require(manifest.get("truth_used") is False, "artifact claims truth usage")
    require(manifest.get("evaluator_used") is False, "artifact claims evaluator usage")
    require(manifest.get("certificate_scope") == "none", "artifact claims a certificate")
    rows = validate_rows(root, manifest)
    events = verify_event_chain(root / EVENTS_NAME, manifest)
    checkpoint = read_json(root / CHECKPOINT_NAME)
    require(
        checkpoint.get("rows_sha256") == manifest.get("rows_sha256"),
        "checkpoint rows binding mismatch",
    )
    verify_checkpoint_prefix(root, checkpoint, events)
    return manifest, rows, events


def verify_immutable_sources(manifest: dict[str, Any], label: str) -> None:
    for key in ("package_commit", "harness_commit"):
        value = manifest.get(key)
        require(
            isinstance(value, str)
            and len(value) == 40
            and all(character in "0123456789abcdef" for character in value),
            f"{label} is missing immutable {key}",
        )
    source_hash = manifest.get("assignment_expansion_sha256")
    require(
        isinstance(source_hash, str)
        and len(source_hash) == 64
        and all(character in "0123456789abcdef" for character in source_hash),
        f"{label} is missing immutable assignment_expansion_sha256",
    )
    require(
        manifest["package_commit"] == manifest["harness_commit"],
        f"{label} package and harness commits differ internally",
    )
    module_value = manifest.get("assignment_expansion_source")
    package_value = manifest.get("package_source")
    runner_hash = manifest.get("runner_sha256")
    if module_value is None and package_value is None and runner_hash is None:
        return
    require(
        isinstance(module_value, str) and isinstance(package_value, str),
        f"{label} source paths are incomplete",
    )
    module_path = Path(cast(str, module_value))
    package_path = Path(cast(str, package_value))
    require(module_path.is_file(), f"{label} assignment source is missing")
    require(package_path.is_file(), f"{label} package source is missing")
    require(
        sha256_file(module_path) == manifest["assignment_expansion_sha256"],
        f"{label} assignment source hash differs",
    )
    require(
        isinstance(runner_hash, str) and len(runner_hash) == 64, f"{label} runner hash is missing"
    )
    repo_root = package_path.resolve().parents[1]
    commit = str(manifest["package_commit"])
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    require(exists.returncode == 0, f"{label} immutable package commit is unavailable")
    module_relative = module_path.resolve().relative_to(repo_root.resolve())
    module_bytes = subprocess.check_output(
        ["git", "show", f"{commit}:{module_relative.as_posix()}"], cwd=repo_root
    )
    require(
        hashlib.sha256(module_bytes).hexdigest() == manifest["assignment_expansion_sha256"],
        f"{label} assignment source does not match immutable commit",
    )
    runner_matches = []
    for relative in (
        Path("tools/run_assignment_expansion_diagnostic.py"),
        Path("tools/run_parallel_assignment_expansion_diagnostic.py"),
    ):
        try:
            data = subprocess.check_output(
                ["git", "show", f"{commit}:{relative.as_posix()}"],
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            continue
        runner_matches.append(hashlib.sha256(data).hexdigest() == runner_hash)
    require(any(runner_matches), f"{label} runner source does not match immutable commit")


def compare_bindings(serial: dict[str, Any], parallel: dict[str, Any]) -> None:
    verify_immutable_sources(serial, "serial")
    verify_immutable_sources(parallel, "parallel")
    for key in (
        "assignment_expansion_sha256",
        "frozen_scoring_source_sha",
        "assigned_manifest_sha256",
        "a20_manifest_sha256",
        "a20_rows_sha256",
    ):
        require(
            serial.get(key) == parallel.get(key),
            f"shared source binding differs for {key}",
        )
    require(serial.get("source_pins") == parallel.get("source_pins"), "source_pins differ")
    require(serial.get("input_paths") == parallel.get("input_paths"), "input_paths differ")
    provenance_raw = parallel.get("worker_provenance")
    require(
        type(provenance_raw) is list and len(provenance_raw) == 4,
        "parallel worker provenance is incomplete",
    )
    provenance = cast(list[dict[str, Any]], provenance_raw)
    worker_ids = [item.get("worker_id") for item in provenance]
    require(len(set(worker_ids)) == 4, "parallel worker IDs are not unique")
    for item in provenance:
        require(
            item.get("package_commit") == parallel.get("package_commit"),
            "worker package commit differs",
        )
        require(
            item.get("assignment_expansion_sha256") == parallel.get("assignment_expansion_sha256"),
            "worker assignment source hash differs",
        )
        scorer_source = Path(str(item.get("scorer_source", "")))
        require(scorer_source.is_file(), "worker scorer source is missing")
        require(
            sha256_file(scorer_source) == item.get("scorer_sha256"), "worker scorer hash differs"
        )


def choose_input_start(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    alternatives_raw = row.get("start_alternatives")
    require(
        type(alternatives_raw) is list and len(alternatives_raw) == 2,
        "mode does not retain two starts",
    )
    alternatives = cast(list[dict[str, Any]], alternatives_raw)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for item in alternatives:
        hypothesis_raw = item.get("canonical_hypothesis")
        require(type(hypothesis_raw) is dict, "start canonical hypothesis is missing")
        hypothesis = cast(dict[str, Any], hypothesis_raw)
        score_raw = hypothesis.get("score")
        label_raw = item.get("label")
        require(
            type(score_raw) in (int, float) and type(label_raw) is str,
            "invalid start score or label",
        )
        scored.append((float(cast(float, score_raw)), cast(str, label_raw), hypothesis))
    score, label, hypothesis = min(scored, key=lambda item: (item[0], item[1]))
    require(
        row.get("selected_start_label") == label, "selected start is not the score-only minimum"
    )
    require(row.get("seed") == hypothesis, "seed does not equal selected canonical start")
    return label, hypothesis


def compare_mode_rows(
    serial_rows: list[dict[str, Any]], parallel_rows: list[dict[str, Any]]
) -> None:
    require(
        len(serial_rows) <= len(parallel_rows) == MODE_COUNT,
        "parallel does not contain all 64 modes",
    )
    require(len(serial_rows) == 60, "serial artifact is not the expected 60-mode prefix")
    for index, (serial_row, parallel_row) in enumerate(
        zip(serial_rows, parallel_rows[: len(serial_rows)], strict=True)
    ):
        require(
            serial_row.get("mode_index") == parallel_row.get("mode_index") == index,
            "mode IDs differ",
        )
        choose_input_start(serial_row)
        choose_input_start(parallel_row)
        require(
            strip_timing(serial_row, top_level=True) == strip_timing(parallel_row, top_level=True),
            f"mode {index} outcome differs outside timing fields",
        )
    for row in parallel_rows[60:]:
        choose_input_start(row)


def validate_event_inventory(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    indexed = event_index(records)
    allowed = set(range(len(rows)))
    if len(rows) < MODE_COUNT:
        allowed.add(len(rows))
    require(
        {key[0] for key in indexed}.issubset(allowed), "event mode is outside the durable row scope"
    )
    for row in rows:
        mode = row["mode_index"]
        result_count = sum(1 for key in indexed if key[0] == mode and key[1] == "branch_result")
        require(
            result_count == row["branch_event_count"],
            f"mode {mode} event inventory differs from durable row",
        )


def compare_events(
    serial_events: list[dict[str, Any]], parallel_events: list[dict[str, Any]]
) -> None:
    serial_index = event_index(serial_events)
    parallel_index = event_index(parallel_events)
    require(
        set(serial_index).issubset(parallel_index), "parallel event ledger is missing serial events"
    )
    for key, serial_payload in serial_index.items():
        parallel_payload = parallel_index[key]
        serial_normalized = {
            name: value
            for name, value in serial_payload.items()
            if name not in {"mode_index", "phase"}
        }
        parallel_normalized = {
            name: value
            for name, value in parallel_payload.items()
            if name not in {"mode_index", "phase"}
        }
        require(
            serial_normalized == parallel_normalized,
            f"branch payload differs for mode/phase/branch {key}",
        )
    for mode in range(60):
        serial_keys = {key for key in serial_index if key[0] == mode}
        parallel_keys = {key for key in parallel_index if key[0] == mode}
        require(serial_keys == parallel_keys, f"completed mode {mode} branch inventory differs")
    mode60_serial = {key for key in serial_index if key[0] == 60}
    require(bool(mode60_serial), "serial partial mode 60 has no branch events")
    require(
        {key for key in parallel_index if key[0] == 60}.issuperset(mode60_serial),
        "parallel mode 60 does not contain the serial partial prefix",
    )


def evaluate(
    serial_root: Path, parallel_root: Path, audit_path: Path | None = None
) -> dict[str, Any]:
    serial_manifest, serial_rows, serial_events = validate_manifest(serial_root)
    parallel_manifest, parallel_rows, parallel_events = validate_manifest(parallel_root)
    require(serial_manifest.get("status") == "interrupted", "serial artifact is not interrupted")
    require(
        serial_manifest.get("completed_mode_count") == 60, "serial artifact is not a 60-mode prefix"
    )
    require(serial_manifest.get("pending_mode_count") == 4, "serial pending mode count is not four")
    require(parallel_manifest.get("status") == "completed", "parallel artifact is not complete")
    require(
        parallel_manifest.get("completed_mode_count") == MODE_COUNT,
        "parallel artifact is not 64 complete modes",
    )
    require(
        parallel_manifest.get("pending_mode_count", 0) == 0, "parallel artifact has pending modes"
    )
    compare_bindings(serial_manifest, parallel_manifest)
    compare_mode_rows(serial_rows, parallel_rows)
    validate_event_inventory(serial_events, serial_rows)
    validate_event_inventory(parallel_events, parallel_rows)
    compare_events(serial_events, parallel_events)
    audit_ok = False
    audit_reason = "full audit artifact not supplied"
    if audit_path is not None:
        audit = read_json(audit_path)
        mode_evaluations = audit.get("mode_evaluations")
        mode_evaluation_ids = (
            [item.get("mode_index") for item in mode_evaluations]
            if isinstance(mode_evaluations, list)
            and all(isinstance(item, dict) for item in mode_evaluations)
            else []
        )
        audit_event_counts = audit.get("event_mode_counts")
        event_counts_match = False
        if isinstance(audit_event_counts, dict):
            expected_event_counts = {
                str(mode): {
                    "started": sum(
                        1
                        for record in parallel_events
                        if record.get("mode_index") == mode
                        and record.get("phase") == "branch_started"
                    ),
                    "result": sum(
                        1
                        for record in parallel_events
                        if record.get("mode_index") == mode
                        and record.get("phase") == "branch_result"
                    ),
                    "failure": sum(
                        1
                        for record in parallel_events
                        if record.get("mode_index") == mode and record.get("phase") == "failure"
                    ),
                }
                for mode in range(MODE_COUNT)
            }
            event_counts_match = audit_event_counts == expected_event_counts
        audit_ok = (
            audit.get("evaluation_status") == "complete_audit"
            and audit.get("branch_rescore_complete") is True
            and audit.get("evaluation_only") is True
            and audit.get("truth_used_for_search_or_proposal_selection") is False
            and audit.get("certificate_scope") == "none"
            and audit.get("mode_count") == MODE_COUNT
            and audit.get("completed_mode_count") == MODE_COUNT
            and audit.get("pending_mode_count") == 0
            and audit.get("expansion_status") == "completed"
            and audit.get("expansion_event_count") == parallel_manifest.get("event_count")
            and audit.get("expansion_event_digest") == parallel_manifest.get("event_digest")
            and audit.get("expansion_events_sha256") == parallel_manifest.get("events_sha256")
            and audit.get("expansion_rows_sha256") == parallel_manifest.get("rows_sha256")
            and audit.get("expansion_manifest_sha256")
            == sha256_file(parallel_root / "manifest.json")
            and mode_evaluation_ids == list(range(MODE_COUNT))
            and event_counts_match
        )
        if not audit_ok:
            audit_reason = (
                "full audit is missing exact source hashes, branch payload hashes, "
                "or complete 64-mode branch rescoring"
            )
    return {
        "schema": "parallel-assignment-expansion-evaluation-v1",
        "evaluation_status": "complete_audit" if audit_ok else "provisional",
        "branch_rescore_complete": audit_ok,
        "rescore_missing_reason": None if audit_ok else audit_reason,
        "truth_used": False,
        "mode_count": MODE_COUNT,
        "serial_completed_mode_count": len(serial_rows),
        "parallel_completed_mode_count": len(parallel_rows),
        "serial_event_count": len(serial_events),
        "parallel_event_count": len(parallel_events),
        "serial_manifest_sha256": sha256_file(serial_root / "manifest.json"),
        "parallel_manifest_sha256": sha256_file(parallel_root / "manifest.json"),
        "source_bindings": {
            key: serial_manifest.get(key)
            for key in (
                "package_commit",
                "harness_commit",
                "assignment_expansion_sha256",
                "frozen_scoring_source_sha",
            )
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", type=Path, required=True)
    parser.add_argument("--parallel", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.serial, args.parallel, args.audit)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
