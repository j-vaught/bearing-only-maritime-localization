"""Run the bounded assignment-conditioned refinement diagnostic.

The runner consumes only the completed A20 cached-mode artifact. It does not
read truth, evaluator outputs, or select new seeds. Every proposal is
re-scored by the current canonical CandidateScorer before it can become a
subsequent start. The artifact covers exactly the preselected A20 modes.
"""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import fcntl
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Make direct `python tools/...` invocation resolve the repository package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Set BLAS and numerical worker pools before SciPy and NumPy are imported.
for _thread_env in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_env] = "1"

from bearing_localization.search.assigned_bearing_refinement import (  # noqa: E402
    AssignedBearingRefinementResult,
    refine_assigned_bearing,
)
from bearing_localization.search.config import SearchConfig  # noqa: E402
from bearing_localization.search.scoring import CandidateScorer  # noqa: E402
from maritime_localization_shared.contracts.inputs_v2 import (  # noqa: E402
    CatalogV2,
    MapV2,
    MeasurementSetV2,
)
from maritime_localization_shared.contracts.localization import Hypothesis  # noqa: E402
from maritime_localization_shared.geospatial.vector_map import VectorMap  # noqa: E402
from maritime_localization_shared.serialization import contract_from_dict, read_contract  # noqa: E402
from tools.refine_cached_modes import (  # noqa: E402
    FLOOR as A20_FLOOR,
    Seed,
    load_committed,
    read_json,
    sha256,
    write_json,
)

FLOOR = A20_FLOOR
MODE_COUNT = 64
MAX_STEPS = 5
MIN_DISPLACEMENT_M = 0.01
SCORE_ATOL = 1e-8
SCORE_RTOL = 1e-10
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


def canonical_lock_path() -> Path:
    common = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], cwd=_REPO_ROOT, text=True
        ).strip()
    )
    if not common.is_absolute():
        common = (_REPO_ROOT / common).resolve()
    return common.resolve().parent / ".agent-work" / "benchmark.lock"


@dataclass(frozen=True, slots=True)
class ModeRow:
    mode_index: int
    input_index: int
    candidate_id: str
    chunk_name: str
    chunk_sha256: str
    sidecar_sha256: str
    cached_hypothesis: Hypothesis
    refined_hypothesis: Hypothesis


@dataclass(frozen=True, slots=True)
class StartRun:
    label: str
    initial_hypothesis: Hypothesis
    steps: tuple[dict[str, Any], ...]
    final_hypothesis: Hypothesis
    stop_reason: str


class DiagnosticInputError(ValueError):
    """An immutable input or source-row contract was violated."""


def _hypothesis(value: Any, field: str) -> Hypothesis:
    if type(value) is not dict:
        raise DiagnosticInputError(f"{field} must be a Hypothesis mapping")
    try:
        result = contract_from_dict(Hypothesis, value)
    except (TypeError, ValueError, KeyError) as exc:
        raise DiagnosticInputError(f"{field} is not a valid Hypothesis") from exc
    if not isinstance(result, Hypothesis):
        raise DiagnosticInputError(f"{field} did not decode to Hypothesis")
    return result


def _assignment_ties(hypothesis: Hypothesis) -> list[dict[str, Any]]:
    return [
        {
            "report_index": item.report_index,
            "catalog_key": item.catalog_key,
            "residual_deg": item.residual_deg,
            "cost": item.cost,
        }
        for item in hypothesis.assignments
    ]


def _same_assignments(left: Hypothesis, right: Hypothesis) -> bool:
    return _assignment_ties(left) == _assignment_ties(right)


def _same_score(left: float, right: float) -> bool:
    return math.isclose(left, right, abs_tol=SCORE_ATOL, rel_tol=SCORE_RTOL)


def _row_from_dict(row: dict[str, Any], selected: dict[str, Any], ordinal: int) -> ModeRow:
    if row.get("seed_order") != ordinal:
        raise DiagnosticInputError("A20 rows are not in seed-selection order")
    for field in ("candidate_id", "input_index", "chunk_name", "chunk_sha256", "sidecar_sha256"):
        if row.get(field) != selected.get(field):
            raise DiagnosticInputError(f"A20 row source reference mismatch for {field}")
    cached = _hypothesis(row.get("cached_hypothesis"), "cached_hypothesis")
    selected_cached = _hypothesis(selected.get("cached_hypothesis"), "selected cached_hypothesis")
    if cached != selected_cached:
        raise DiagnosticInputError("A20 cached hypothesis differs from selected mode")
    refined_mode = row.get("refined_mode")
    if type(refined_mode) is not dict:
        raise DiagnosticInputError("refined_mode must be a mapping")
    refined = _hypothesis(refined_mode.get("hypothesis"), "refined_mode.hypothesis")
    return ModeRow(
        mode_index=ordinal,
        input_index=int(row["input_index"]),
        candidate_id=str(row["candidate_id"]),
        chunk_name=str(row["chunk_name"]),
        chunk_sha256=str(row["chunk_sha256"]),
        sidecar_sha256=str(row["sidecar_sha256"]),
        cached_hypothesis=cached,
        refined_hypothesis=refined,
    )


def load_a20_bundle(root: Path) -> tuple[list[ModeRow], dict[str, Any]]:
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != "cached-mode-refinement-v1":
        raise DiagnosticInputError("Unexpected A20 manifest schema")
    if manifest.get("status") != "selected_modes_completed":
        raise DiagnosticInputError("A20 artifact is not complete")
    if manifest.get("completed_mode_count") != MODE_COUNT:
        raise DiagnosticInputError("A20 artifact does not contain 64 completed modes")
    if manifest.get("selected_mode_count") != MODE_COUNT:
        raise DiagnosticInputError("A20 selected mode count is not 64")
    selected = manifest.get("selected_modes")
    if type(selected) is not list or len(selected) != MODE_COUNT:
        raise DiagnosticInputError("A20 selected_modes must contain exactly 64 rows")
    rows_file = manifest.get("rows_file")
    if (
        type(rows_file) is not str
        or not rows_file
        or Path(rows_file).is_absolute()
        or Path(rows_file).name != rows_file
        or "/" in rows_file
        or "\\" in rows_file
        or rows_file in (".", "..")
    ):
        raise DiagnosticInputError("A20 rows_file must be one safe basename")
    rows_path = root / rows_file
    if not rows_path.is_file() or sha256(rows_path) != manifest.get("rows_sha256"):
        raise DiagnosticInputError("A20 rows hash mismatch")
    rows: list[ModeRow] = []
    with rows_path.open() as stream:
        for ordinal, line in enumerate(stream):
            if ordinal >= MODE_COUNT:
                raise DiagnosticInputError("A20 rows contain more than 64 modes")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DiagnosticInputError("A20 rows contain invalid JSON") from exc
            if type(row) is not dict:
                raise DiagnosticInputError("A20 row must be an object")
            rows.append(_row_from_dict(row, selected[ordinal], ordinal))
    if len(rows) != MODE_COUNT:
        raise DiagnosticInputError("A20 rows do not contain exactly 64 modes")
    return rows, manifest


def verify_source_pins(
    a20_manifest: dict[str, Any],
    a20_root: Path,
    attempt: Path,
    static: Path,
    config_path: Path,
) -> tuple[dict[str, Any], list[Seed]]:
    expected_attempt = Path(str(a20_manifest.get("source_attempt", ""))).resolve()
    if expected_attempt != attempt.resolve():
        raise DiagnosticInputError("A20 source attempt path differs from requested attempt")
    seeds, bindings = load_committed(attempt)
    identity = bindings["identity"]
    if a20_manifest.get("supervisor_sha256") != sha256(attempt / "supervisor-manifest.json"):
        raise DiagnosticInputError("Original supervisor manifest hash differs from A20")
    if a20_manifest.get("identity") != identity:
        raise DiagnosticInputError("Original run identity differs from A20")
    supervisor = read_json(attempt / "supervisor-manifest.json")
    evidence = a20_manifest.get("source_evidence_sha256")
    expected_evidence = {
        key: supervisor[key]
        for key in (
            "candidate_plan_sha256",
            "candidate_inventory_sha256",
            "candidate_dispositions_sha256",
            "chunk_results_sha256",
            "hypotheses_sha256",
        )
    }
    if evidence != expected_evidence:
        raise DiagnosticInputError("Original evidence pins differ from A20")
    static_hashes = identity.get("static_input_sha256")
    if type(static_hashes) is not dict:
        raise DiagnosticInputError("A20 identity lacks static input hashes")
    input_paths = {name: static / f"{name}.json" for name in ("measurements", "catalog", "map")}
    for name, path in input_paths.items():
        if not path.is_file() or sha256(path) != static_hashes.get(name):
            raise DiagnosticInputError(f"Static input hash mismatch for {name}")
    if not config_path.is_file() or sha256(config_path) != identity.get("scoring_config_sha256"):
        raise DiagnosticInputError("Scoring config hash mismatch")
    by_index = {seed.input_index: seed for seed in seeds}
    if len(by_index) != len(seeds):
        raise DiagnosticInputError("Original committed input indices are not unique")
    return {
        "attempt": str(attempt.resolve()),
        "attempt_supervisor_sha256": sha256(attempt / "supervisor-manifest.json"),
        "attempt_identity_sha256": identity["identity_sha256"],
        "attempt_tree_bindings": bindings,
        "static_input_sha256": static_hashes,
        "config_sha256": identity["scoring_config_sha256"],
        "frozen_scoring_source_sha": identity.get("scoring_source_sha"),
        "input_paths": {name: str(path.resolve()) for name, path in input_paths.items()},
        "config_path": str(config_path.resolve()),
    }, seeds


def validate_mode_source_refs(rows: list[ModeRow], seeds: list[Seed]) -> None:
    by_index = {seed.input_index: seed for seed in seeds}
    for row in rows:
        seed = by_index.get(row.input_index)
        if seed is None:
            raise DiagnosticInputError(
                f"A20 input index is absent from committed attempt: {row.input_index}"
            )
        if (row.candidate_id, row.chunk_name, row.chunk_sha256, row.sidecar_sha256) != (
            seed.candidate_id,
            seed.chunk_name,
            seed.chunk_sha256,
            seed.sidecar_sha256,
        ):
            raise DiagnosticInputError(f"A20 source references are stale for mode {row.mode_index}")
        if row.cached_hypothesis != seed.hypothesis:
            raise DiagnosticInputError(f"A20 cached hypothesis is stale for mode {row.mode_index}")


def build_scorer(static: Path, config_path: Path) -> CandidateScorer:
    config_data = read_json(config_path)
    if config_data.pop("contract_type", None) != "SearchConfig":
        raise DiagnosticInputError("Configuration discriminator mismatch")
    config = SearchConfig(**config_data)
    paths = {name: static / f"{name}.json" for name in ("measurements", "catalog", "map")}
    measurements = read_contract(paths["measurements"], MeasurementSetV2)
    catalog = read_contract(paths["catalog"], CatalogV2)
    map_input = read_contract(paths["map"], MapV2)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    return CandidateScorer(measurements, catalog, vector_map, config)


def revalidate_start(scorer: CandidateScorer, source: Hypothesis) -> Hypothesis:
    evaluation = scorer.evaluate((source.east_m, source.north_m))
    if evaluation.status != "scored" or evaluation.hypothesis is None:
        raise DiagnosticInputError(f"Initial source candidate is {evaluation.status}")
    exact = evaluation.hypothesis
    if not _same_score(exact.score, source.score):
        raise DiagnosticInputError("Initial source score differs from current canonical score")
    if not _same_assignments(exact, source):
        raise DiagnosticInputError(
            "Initial source assignments differ from current canonical assignments"
        )
    return exact


def _result_dict(result: AssignedBearingRefinementResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "numerical_status": result.numerical_status,
        "original_hypothesis": asdict(result.original_hypothesis),
        "proposed_pose": result.proposed_pose,
        "exact_hypothesis": None
        if result.exact_hypothesis is None
        else asdict(result.exact_hypothesis),
        "solver_status": result.solver_status,
        "nfev": result.nfev,
        "njev": result.njev,
        "residual_count": result.residual_count,
        "residual_evaluations": result.residual_evaluations,
        "assignment_count": result.assignment_count,
        "canonical_evaluations": result.canonical_evaluations,
        "message": result.message,
    }


def run_start(
    scorer: CandidateScorer,
    source: Hypothesis,
    label: str,
    proposer: Callable[
        [CandidateScorer, Hypothesis], AssignedBearingRefinementResult
    ] = refine_assigned_bearing,
    max_steps: int = MAX_STEPS,
) -> StartRun:
    initial = revalidate_start(scorer, source)
    current = initial
    steps: list[dict[str, Any]] = []
    stop_reason = "step_limit"
    for step_index in range(max_steps):
        try:
            result = proposer(scorer, current)
        except TimeoutError:
            raise
        except Exception as exc:  # Preserve a solver exception as an explicit stop.
            steps.append(
                {
                    "step": step_index + 1,
                    "status": "proposal_exception",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
            stop_reason = "proposal_exception"
            break
        record = {"step": step_index + 1, **_result_dict(result)}
        if result.status != "improved" or result.exact_hypothesis is None:
            steps.append(record)
            stop_reason = result.status
            break
        proposal = result.proposed_pose
        if proposal is None:
            steps.append(record)
            stop_reason = "invalid_proposal"
            break
        displacement = math.hypot(proposal[0] - current.east_m, proposal[1] - current.north_m)
        record["displacement_m"] = displacement
        if displacement < MIN_DISPLACEMENT_M:
            steps.append(record)
            stop_reason = "displacement_below_threshold"
            break
        if not math.isfinite(result.exact_hypothesis.score):
            raise DiagnosticInputError("Nonfinite proposal score")
        canonical = scorer.evaluate(proposal)
        if canonical.status != "scored" or canonical.hypothesis is None:
            steps.append(
                {"step": step_index + 1, "status": canonical.status, "message": canonical.reason}
            )
            stop_reason = canonical.status
            break
        if canonical.hypothesis.score >= current.score:
            steps.append(
                {
                    "step": step_index + 1,
                    "status": "no_improvement",
                    "message": "canonical re-score did not improve",
                }
            )
            stop_reason = "no_improvement"
            break
        record["exact_hypothesis"] = asdict(canonical.hypothesis)
        record["accepted_for_next_step"] = True
        steps.append(record)
        current = canonical.hypothesis
    else:
        stop_reason = "step_limit"
    return StartRun(label, initial, tuple(steps), current, stop_reason)


def _hypothesis_or_none(value: Hypothesis | None) -> dict[str, Any] | None:
    return None if value is None else asdict(value)


def _start_record(start: StartRun) -> dict[str, Any]:
    return {
        "label": start.label,
        "initial_hypothesis": asdict(start.initial_hypothesis),
        "initial_assignments": _assignment_ties(start.initial_hypothesis),
        "steps": list(start.steps),
        "final_hypothesis": asdict(start.final_hypothesis),
        "final_assignments": _assignment_ties(start.final_hypothesis),
        "stop_reason": start.stop_reason,
    }


def _best_start(starts: list[StartRun]) -> StartRun:
    return min(starts, key=lambda start: (start.final_hypothesis.score, start.label))


def _output_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _check_space(path: Path) -> int:
    free = shutil.disk_usage(path).free
    if free < FLOOR + MAX_OUTPUT_BYTES:
        raise OSError("40 GiB reserve leaves insufficient room for bounded output")
    return free


def _alarm(_signal: int, _frame: Any) -> None:
    raise TimeoutError("whole diagnostic deadline exceeded")


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if args.timeout_s <= 0.0 or args.timeout_s > 1800.0 or not math.isfinite(args.timeout_s):
        raise ValueError("timeout must be finite and within 1800 seconds")
    if args.lock.resolve() != canonical_lock_path().resolve():
        raise ValueError(f"Only the canonical benchmark lock is allowed: {canonical_lock_path()}")
    _check_space(args.output.parent)
    args.output.mkdir(exist_ok=False)
    rows_path = args.output / "assigned-refinement-rows.jsonl"
    checkpoint_path = args.output / "checkpoint.json"
    manifest_path = args.output / "manifest.json"
    manifest: dict[str, Any] = {
        "schema": "assigned-bearing-refinement-diagnostic-v1",
        "status": "started",
        "mode_count": MODE_COUNT,
        "max_steps_per_start": MAX_STEPS,
        "starts_per_mode": ["cached_hypothesis", "refined_mode.hypothesis"],
        "score_atol": SCORE_ATOL,
        "score_rtol": SCORE_RTOL,
        "minimum_displacement_m": MIN_DISPLACEMENT_M,
        "whole_timeout_s": args.timeout_s,
        "certificate_scope": "none",
        "decision_status": "ambiguous",
        "full_domain_complete": False,
        "truth_used": False,
        "evaluator_used": False,
        "cpu_only": True,
        "numeric_threads": 1,
        "rows_file": rows_path.name,
        "checkpoint_file": checkpoint_path.name,
        "completed_mode_count": 0,
    }
    write_json(manifest_path, manifest)
    completed: list[dict[str, Any]] = []
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, args.timeout_s)
        for name in (
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            os.environ[name] = "1"
        with args.lock.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("canonical benchmark lock is already held") from exc
            rows, a20_manifest = load_a20_bundle(args.a20)
            source_pins, seeds = verify_source_pins(
                a20_manifest, args.a20, args.attempt, args.static, args.config
            )
            validate_mode_source_refs(rows, seeds)
            manifest["a20_root"] = str(args.a20.resolve())
            manifest["a20_manifest_sha256"] = sha256(args.a20 / "manifest.json")
            manifest["a20_rows_sha256"] = a20_manifest["rows_sha256"]
            manifest["source_pins"] = source_pins
            manifest["harness_commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
            ).strip()
            manifest["package_commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
            ).strip()
            scorer = build_scorer(args.static, args.config)
            write_json(manifest_path, manifest)
            with rows_path.open("w") as stream:
                for row in rows:
                    _check_space(args.output)
                    starts = [
                        run_start(scorer, row.cached_hypothesis, "cached_hypothesis"),
                        run_start(scorer, row.refined_hypothesis, "refined_mode.hypothesis"),
                    ]
                    best = _best_start(starts)
                    output_row = {
                        "mode_index": row.mode_index,
                        "input_index": row.input_index,
                        "candidate_id": row.candidate_id,
                        "chunk_name": row.chunk_name,
                        "chunk_sha256": row.chunk_sha256,
                        "sidecar_sha256": row.sidecar_sha256,
                        "starts": [_start_record(start) for start in starts],
                        "best_hypothesis": asdict(best.final_hypothesis),
                        "best_source": best.label,
                    }
                    stream.write(
                        json.dumps(
                            output_row, sort_keys=True, allow_nan=False, separators=(",", ":")
                        )
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                    completed.append(output_row)
                    checkpoint = {
                        "schema": "assigned-bearing-refinement-checkpoint-v1",
                        "status": "in_progress",
                        "completed_mode_count": len(completed),
                        "rows_sha256": sha256(rows_path),
                    }
                    write_json(checkpoint_path, checkpoint)
                    manifest["completed_mode_count"] = len(completed)
                    manifest["rows_sha256"] = sha256(rows_path)
                    manifest["elapsed_s"] = time.perf_counter() - started
                    write_json(manifest_path, manifest)
                    if _output_bytes(args.output) > MAX_OUTPUT_BYTES:
                        raise OSError("diagnostic output exceeded 5 MiB")
            manifest["status"] = "completed"
            manifest["ranked_global_best"] = [
                {
                    "mode_index": item["mode_index"],
                    "candidate_id": item["candidate_id"],
                    "best_source": item["best_source"],
                    "score": item["best_hypothesis"]["score"],
                    "east_m": item["best_hypothesis"]["east_m"],
                    "north_m": item["best_hypothesis"]["north_m"],
                    "assignments": item["best_hypothesis"]["assignments"],
                }
                for item in sorted(
                    completed,
                    key=lambda item: (item["best_hypothesis"]["score"], item["mode_index"]),
                )
            ]
            write_json(
                checkpoint_path,
                {
                    "schema": "assigned-bearing-refinement-checkpoint-v1",
                    "status": "completed",
                    "completed_mode_count": len(completed),
                    "rows_sha256": sha256(rows_path),
                },
            )
    except TimeoutError as exc:
        manifest["status"] = "interrupted"
        manifest["error"] = str(exc)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        manifest["completed_mode_count"] = len(completed)
        manifest["elapsed_s"] = time.perf_counter() - started
        if rows_path.exists():
            manifest["rows_sha256"] = sha256(rows_path)
        manifest["output_bytes"] = _output_bytes(args.output)
        if manifest["output_bytes"] > MAX_OUTPUT_BYTES:
            manifest["status"] = "failed"
            manifest["error"] = "diagnostic output exceeded 5 MiB"
        manifest["free_bytes_at_end"] = shutil.disk_usage(args.output).free
        write_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    for name in ("a20", "attempt", "static", "config", "output", "lock"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or not 0.0 < args.timeout_s <= 1800.0:
        parser.error("--timeout-s must be finite and in (0, 1800]")
    result = run(args)
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "completed_mode_count": result.get("completed_mode_count"),
            }
        )
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
