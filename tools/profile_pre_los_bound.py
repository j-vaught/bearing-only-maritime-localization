"""Measure the experimental A14/A21 pre-LOS bound on a frozen score-only sample."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import resource
import subprocess
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.pre_los_bound import pre_los_catalog_row_min_bound
from bearing_localization.search.pre_los_preparation import prepare_pre_los_targets
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

ABS_TOL = 1e-8
REL_TOL = 1e-10
THREAD_KEYS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
SCORE_CONFIG_KEYS = (
    "objective_version",
    "min_range_m",
    "max_range_m",
    "range_inclusive",
    "circular_merge_tolerance_deg",
    "generated_sigma_deg",
    "sigma_floor_deg",
    "detection_probability",
    "bearing_outlier_probability",
    "expected_unmatched_reports",
    "correct_type_probability",
    "use_types",
    "common_bearing_bias_prior_deg",
    "absolute_score_tolerance",
    "relative_score_tolerance",
)


@dataclass(frozen=True, slots=True)
class CandidatePoint:
    candidate_id: str
    position_xy_m: tuple[float, float]


@dataclass(frozen=True, slots=True)
class CandidateDisposition:
    candidate_id: str
    position_xy_m: tuple[float, float]
    action: str
    exact_status: str | None
    score: float | None
    lower_bound: float | None
    bound_supported: bool | None
    preparation_supported: bool | None
    coverage_certified: bool | None
    incumbent_score_before: float
    pruning_margin: float
    preparation_reason: str | None
    preparation_elapsed_s: float
    bound_elapsed_s: float
    exact_elapsed_s: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scoring_config_projection(values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    normalized.pop("contract_type", None)
    normalized.pop("proposal_cache_path", None)
    config = SearchConfig(**normalized)
    return {key: getattr(config, key) for key in SCORE_CONFIG_KEYS}


def load_truth_free_expanded_incumbent(
    manifest_path: Path,
    rows_path: Path,
    *,
    expected_static_hashes: dict[str, str],
    active_config: dict[str, Any],
    expected_score: float,
) -> tuple[CandidatePoint, float, dict[str, Any]]:
    """Select and validate the lowest-score completed row from truth-free raw output."""
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "assignment-expansion-diagnostic-v1":
        raise ValueError("Expanded incumbent manifest has an unexpected schema")
    if manifest.get("truth_used") is not False or manifest.get("evaluator_used") is not False:
        raise ValueError("Expanded incumbent source must explicitly exclude truth and evaluation")
    rows_sha = sha256_file(rows_path)
    if rows_sha != manifest.get("rows_sha256"):
        raise ValueError("Expanded incumbent rows do not match the saved manifest hash")
    source_input_hashes = (
        manifest.get("source_pins", {})
        .get("attempt_tree_bindings", {})
        .get("identity", {})
        .get("static_input_sha256")
    )
    expected = {
        "measurements": expected_static_hashes["measurements.json"],
        "catalog": expected_static_hashes["catalog.json"],
        "map": expected_static_hashes["map.json"],
    }
    if source_input_hashes != expected:
        raise ValueError("Expanded incumbent uses different measurement, catalog, or map inputs")
    source_config_pins = manifest.get("source_pins", {})
    source_config_path = Path(source_config_pins.get("config_path", manifest["config_path"]))
    expected_config_hash = source_config_pins.get("config_sha256", manifest.get("config_sha256"))
    if expected_config_hash is None or sha256_file(source_config_path) != expected_config_hash:
        raise ValueError("Expanded incumbent scoring configuration hash is invalid")
    source_config = json.loads(source_config_path.read_text())
    source_projection = _scoring_config_projection(source_config)
    active_projection = _scoring_config_projection(active_config)
    if source_projection != active_projection:
        raise ValueError("Expanded incumbent scoring objective/config differs from this profile")
    excluded_config_fields = {"candidate_budget", "proposal_cache_path"}
    config_differences = {
        key: {"source": source_config.get(key), "profile": active_config.get(key)}
        for key in sorted(set(source_config) | set(active_config))
        if source_config.get(key) != active_config.get(key)
    }
    if any(key not in excluded_config_fields for key in config_differences):
        raise ValueError("Expanded incumbent differs in a non-search scoring configuration field")
    rows: list[tuple[float, int, dict[str, Any]]] = []
    for row_index, line in enumerate(rows_path.read_text().splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        hypothesis = row.get("best_hypothesis")
        if row.get("completed") is not True or row.get("budget_exhausted") is True:
            continue
        if not isinstance(hypothesis, dict) or not isinstance(row.get("candidate_id"), str):
            continue
        score = hypothesis.get("score")
        east, north = hypothesis.get("east_m"), hypothesis.get("north_m")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or isinstance(east, bool)
            or isinstance(north, bool)
            or not isinstance(east, (int, float))
            or not isinstance(north, (int, float))
            or not math.isfinite(east)
            or not math.isfinite(north)
        ):
            continue
        rows.append((float(score), row_index, row))
    if not rows:
        raise ValueError("Expanded incumbent source contains no completed finite score rows")
    raw_score, row_index, winning_row = min(rows, key=lambda item: (item[0], item[1]))
    if not math.isclose(raw_score, expected_score, abs_tol=ABS_TOL, rel_tol=REL_TOL):
        raise ValueError("Truth-free expanded scorewinner differs from expected diagnostic value")
    hypothesis = winning_row["best_hypothesis"]
    point = CandidatePoint(
        winning_row["candidate_id"],
        (float(hypothesis["east_m"]), float(hypothesis["north_m"])),
    )
    provenance = {
        "manifest_sha256": sha256_file(manifest_path),
        "rows_sha256": rows_sha,
        "source_config_sha256": expected_config_hash,
        "raw_winner_row_index": row_index,
        "raw_winner_mode_index": winning_row.get("mode_index"),
        "raw_winner_candidate_id": point.candidate_id,
        "raw_winner_declared_score": raw_score,
        "raw_winner_position_xy_m": list(point.position_xy_m),
        "completed_score_row_count": len(rows),
        "source_truth_used": False,
        "source_evaluator_used": False,
        "scoring_config_projection_matches": True,
        "search_only_config_differences": config_differences,
    }
    return point, raw_score, provenance


def _activity() -> dict[str, Any]:
    result: dict[str, Any] = {"loadavg": None, "cpu_processes": [], "gpu_processes": []}
    try:
        result["loadavg"] = Path("/proc/loadavg").read_text().strip()
        output = subprocess.run(
            ["ps", "-eo", "pid=,pcpu=,pmem=,comm="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        rows = []
        for line in output.splitlines():
            fields = line.split(None, 3)
            if len(fields) == 4 and float(fields[1]) > 1.0:
                rows.append(
                    {
                        "pid": int(fields[0]),
                        "pcpu": float(fields[1]),
                        "pmem": float(fields[2]),
                        "comm": fields[3],
                    }
                )
        result["cpu_processes"] = rows[:32]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        result["gpu_processes"] = output.splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def _exact(scorer: CandidateScorer, position: tuple[float, float]):
    return scorer.evaluate(position)


def _best_and_ties(
    scores: dict[str, float], ordered_ids: Sequence[str]
) -> tuple[float | None, list[str]]:
    """Return the best finite score and its tied IDs, or an empty result."""
    if not scores:
        return None, []
    best = min(scores.values())
    tied = [
        candidate_id
        for candidate_id in ordered_ids
        if candidate_id in scores
        and math.isclose(scores[candidate_id], best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
    ]
    return best, tied


def _isclose_tie_margin(best_score: float, *, abs_tol: float, rel_tol: float) -> float:
    """Return a strict score gap outside math.isclose's tie band."""
    if not 0.0 <= rel_tol < 1.0 or abs_tol < 0.0:
        raise ValueError("score tolerances must be nonnegative and relative tolerance below one")
    boundary = max(abs_tol, rel_tol * abs(best_score) / (1.0 - rel_tol))
    return boundary + 2.0 * math.ulp(best_score)


def pre_los_screen(
    scorer: CandidateScorer,
    candidates: Sequence[CandidatePoint],
    *,
    incumbent_id: str | None = None,
    saved_incumbent_score: float,
    external_incumbent: CandidatePoint | None = None,
) -> tuple[tuple[CandidateDisposition, ...], dict[str, Any]]:
    """Experimental serial controller; every unsupported case uses exact scoring."""
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    if (incumbent_id is None) == (external_incumbent is None):
        raise ValueError("provide exactly one in-sample or external incumbent")
    if not math.isfinite(saved_incumbent_score):
        raise ValueError("a finite saved exact incumbent score is required")
    if external_incumbent is None:
        seed = by_id.get(incumbent_id)
        if seed is None:
            raise ValueError("saved exact incumbent ID is absent from the candidate sample")
        incumbent_key = seed.candidate_id
    else:
        seed = external_incumbent
        if seed.candidate_id in by_id:
            raise ValueError("external incumbent ID must be outside the candidate sample")
        if any(candidate.position_xy_m == seed.position_xy_m for candidate in candidates):
            raise ValueError("external incumbent position must be outside the candidate sample")
        incumbent_key = seed.candidate_id
    timing: dict[str, float] = {
        "incumbent_validation_s": 0.0,
        "preparation_s": 0.0,
        "bound_s": 0.0,
        "exact_fallback_s": 0.0,
    }
    exact_start = time.perf_counter()
    seed_evaluation = _exact(scorer, seed.position_xy_m)
    timing["incumbent_validation_s"] = time.perf_counter() - exact_start
    if seed_evaluation.status != "scored" or seed_evaluation.hypothesis is None:
        raise ValueError("saved incumbent is not exactly scoreable by current scorer")
    incumbent_score = float(seed_evaluation.hypothesis.score)
    if not math.isclose(incumbent_score, saved_incumbent_score, abs_tol=ABS_TOL, rel_tol=REL_TOL):
        raise ValueError("revalidated incumbent score differs from score-only artifact")

    dispositions: list[CandidateDisposition] = []
    exact_results: dict[str, Any] = {incumbent_key: seed_evaluation}
    margins: list[float] = []
    for candidate in candidates:
        if external_incumbent is None and candidate.candidate_id == incumbent_id:
            dispositions.append(
                CandidateDisposition(
                    candidate.candidate_id,
                    candidate.position_xy_m,
                    "exact_incumbent",
                    seed_evaluation.status,
                    incumbent_score,
                    None,
                    None,
                    None,
                    None,
                    incumbent_score,
                    0.0,
                    None,
                    0.0,
                    0.0,
                    0.0,
                )
            )
            continue
        margin = max(
            _isclose_tie_margin(
                incumbent_score,
                abs_tol=scorer.config.absolute_score_tolerance,
                rel_tol=scorer.config.relative_score_tolerance,
            ),
            _isclose_tie_margin(incumbent_score, abs_tol=ABS_TOL, rel_tol=REL_TOL),
        )
        margins.append(margin)
        incumbent_score_before = incumbent_score
        water_status = scorer.vector_map.point_water_status(candidate.position_xy_m)
        prep_elapsed = bound_elapsed = exact_elapsed = 0.0
        prep_supported: bool | None = None
        coverage_certified: bool | None = None
        prep_reason: str | None = None
        lower_value: float | None = None
        bound_supported: bool | None = None
        action = "exact_fallback"
        evaluation = None
        if water_status == "water":
            start = time.perf_counter()
            prepared = prepare_pre_los_targets(scorer, candidate.position_xy_m)
            prep_elapsed = time.perf_counter() - start
            timing["preparation_s"] += prep_elapsed
            prep_supported = (
                prepared.supported
                and prepared.matches_scorer(scorer)
                and prepared.matches_inputs(scorer.measurements, scorer.config)
            )
            coverage_certified = bool(
                prepared.coverage_certificate is not None
                and prepared.coverage_map_context is not None
                and prep_supported
            )
            prep_reason = prepared.reason
            if prep_supported:
                start = time.perf_counter()
                bound = pre_los_catalog_row_min_bound(scorer.measurements, prepared, scorer.config)
                bound_elapsed = time.perf_counter() - start
                timing["bound_s"] += bound_elapsed
                bound_supported = bound.supported
                lower_value = bound.bound
                if (
                    bound.supported
                    and bound.bound is not None
                    and bound.bound > incumbent_score + margin
                ):
                    action = "proposed_prune"
                else:
                    action = "exact_fallback"
            else:
                prep_reason = prep_reason or "preparation bundle failed scorer/input binding"
        start = time.perf_counter()
        if action != "proposed_prune":
            evaluation = _exact(scorer, candidate.position_xy_m)
            exact_elapsed = time.perf_counter() - start
            timing["exact_fallback_s"] += exact_elapsed
            exact_results[candidate.candidate_id] = evaluation
            if evaluation.status == "scored" and evaluation.hypothesis is not None:
                incumbent_score = min(incumbent_score, float(evaluation.hypothesis.score))
            action = (
                "exact_scored" if evaluation.status == "scored" else f"exact_{evaluation.status}"
            )
        dispositions.append(
            CandidateDisposition(
                candidate.candidate_id,
                candidate.position_xy_m,
                action,
                evaluation.status if evaluation else None,
                float(evaluation.hypothesis.score)
                if evaluation and evaluation.hypothesis
                else None,
                lower_value,
                bound_supported,
                prep_supported,
                coverage_certified,
                incumbent_score_before,
                margin,
                prep_reason,
                prep_elapsed,
                bound_elapsed,
                exact_elapsed,
            )
        )
    timing["margin_min"] = min(margins, default=0.0)
    timing["margin_max"] = max(margins, default=0.0)
    return tuple(dispositions), {
        "stage_seconds": timing,
        "exact_results": exact_results,
        "incumbent_candidate_id": incumbent_key,
        "incumbent_position_xy_m": list(seed.position_xy_m),
        "incumbent_score": incumbent_score,
        "incumbent_outside_candidate_set": external_incumbent is not None,
        "candidate_count": len(candidates),
    }


def independently_validate_prunes(
    scorer: CandidateScorer,
    pruned: Sequence[CandidateDisposition],
    *,
    exact_best_score: float,
) -> tuple[dict[str, Any], float]:
    """Exact-score every proposed omission and reject invalid or tied prunes."""
    if len({item.candidate_id for item in pruned}) != len(pruned):
        raise AssertionError("pruned candidate IDs must be unique")
    started = time.perf_counter()
    validations: dict[str, Any] = {}
    for item in pruned:
        if (
            not item.preparation_supported
            or not item.coverage_certified
            or not item.bound_supported
            or item.lower_bound is None
        ):
            raise AssertionError(f"Pruned candidate {item.candidate_id} lacks a certified bound")
        evaluation = scorer.evaluate(item.position_xy_m)
        validations[item.candidate_id] = evaluation
        if evaluation.status != "scored" or evaluation.hypothesis is None:
            raise AssertionError(f"Pruned candidate {item.candidate_id} is not exactly scoreable")
        if item.lower_bound > evaluation.hypothesis.score + 1e-8:
            raise AssertionError(f"Pruned bound exceeds exact score for {item.candidate_id}")
        if math.isclose(
            evaluation.hypothesis.score,
            exact_best_score,
            abs_tol=ABS_TOL,
            rel_tol=REL_TOL,
        ):
            raise AssertionError(f"Pruned candidate {item.candidate_id} is an exact-score tie")
    return validations, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--score-only", type=Path, required=True, help="saved A22 direct-exact artifact"
    )
    parser.add_argument(
        "--profile-metadata",
        type=Path,
        required=True,
        help="cached-profile input-hash and exact-audit artifact",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--incumbent-manifest", type=Path)
    parser.add_argument("--incumbent-rows", type=Path)
    parser.add_argument("--expected-incumbent-score", type=float)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(".agent-work/benchmark.lock"),
        help="canonical benchmark lock; execution fails immediately if held",
    )
    parser.add_argument("--run-screening", action="store_true")
    args = parser.parse_args()
    incumbent_args = (args.incumbent_manifest, args.incumbent_rows, args.expected_incumbent_score)
    if any(value is not None for value in incumbent_args) and not all(
        value is not None for value in incumbent_args
    ):
        parser.error("incumbent manifest, rows, and expected score must be supplied together")
    lock_fd = os.open(args.lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"benchmark lock is held: {args.lock}", file=__import__("sys").stderr)
        os.close(lock_fd)
        return 2
    started = time.perf_counter()
    selection = json.loads(args.selected.read_text())
    score_artifact = json.loads(args.score_only.read_text())
    profile_metadata = json.loads(args.profile_metadata.read_text())
    candidates = tuple(
        CandidatePoint(candidate_id, tuple(position))
        for candidate_id, position in zip(
            selection["selected_ids"], selection["selected_positions_map_epsg32618"], strict=True
        )
    )
    exact_reference = profile_metadata["cases"]["1"]["exact"]
    a22_exact_audits = score_artifact["exact_audits"]
    a22_positions = score_artifact["selected_positions_map_epsg32618"]
    a22_scores = {item["candidate_id"]: item for item in a22_exact_audits}
    score_by_id = {
        item["candidate_id"]: item["score"]
        for item in a22_exact_audits
        if item["score"] is not None and item["status"] == "scored"
    }
    a22_scored_pairs = [
        (item["candidate_id"], float(item["score"]))
        for item in a22_exact_audits
        if item["status"] == "scored" and item["score"] is not None
    ]
    a22_best_score = min(score for _, score in a22_scored_pairs)
    a22_tie_ids = [
        candidate_id
        for candidate_id, score in a22_scored_pairs
        if math.isclose(score, a22_best_score, abs_tol=ABS_TOL, rel_tol=REL_TOL)
    ]
    incumbent_id = a22_tie_ids[0]
    saved_incumbent_score = float(score_by_id[incumbent_id])
    static_hashes = {
        name: sha256_file(args.static / name)
        for name in ("measurements.json", "catalog.json", "map.json", "scope-manifest.json")
    }
    if static_hashes != profile_metadata["input_sha256"]:
        raise ValueError("Frozen score-only input hashes differ from the selected exact artifact")
    selection_hash = sha256_file(args.selected)
    if selection_hash != score_artifact["selected_manifest_sha256"]:
        raise ValueError("Selection manifest hash differs from A22 exact-score artifact")
    config_hash = sha256_file(args.config)
    config_data = json.loads(args.config.read_text())
    config_data.pop("contract_type", None)
    config_data.pop("proposal_cache_path", None)
    if config_hash != score_artifact["config_sha256"]:
        raise ValueError("Scoring configuration hash differs from A22 exact-score artifact")
    scoring_config_hash = hashlib.sha256(
        json.dumps(_scoring_config_projection(config_data), sort_keys=True).encode()
    ).hexdigest()
    for key, expected in profile_metadata["config"].items():
        if config_data.get(key) != expected:
            raise ValueError(f"Effective config field differs from exact reference: {key}")
    if (
        len(a22_exact_audits) != len(candidates)
        or len(a22_positions) != len(candidates)
        or len(a22_scores) != len(candidates)
    ):
        raise ValueError(
            "A22 exact score artifact does not contain one audit per selected candidate"
        )
    if [candidate.candidate_id for candidate in candidates] != [
        item["candidate_id"] for item in exact_reference["audits"]
    ]:
        raise ValueError("Candidate ID order differs from exact score-only artifact")
    for candidate, audit, profile_audit, a22_position in zip(
        candidates, a22_exact_audits, exact_reference["audits"], a22_positions, strict=True
    ):
        if candidate.candidate_id != audit["candidate_id"] or candidate.position_xy_m != tuple(
            a22_position
        ):
            raise ValueError(f"Candidate position/ID mismatch for {candidate.candidate_id}")
        if candidate.candidate_id != profile_audit[
            "candidate_id"
        ] or candidate.position_xy_m != tuple(profile_audit["position_xy_m"]):
            raise ValueError(
                f"Candidate differs from cached-profile audit for {candidate.candidate_id}"
            )
        if audit["status"] != profile_audit["model_status"]:
            raise ValueError(
                f"Candidate status differs from cached-profile audit for {candidate.candidate_id}"
            )
        if audit["score"] is None and profile_audit["score"] is not None:
            raise ValueError(f"Candidate score presence differs for {candidate.candidate_id}")
        if audit["score"] is not None and not math.isclose(
            audit["score"], profile_audit["score"], abs_tol=ABS_TOL, rel_tol=REL_TOL
        ):
            raise ValueError(
                f"Candidate score differs from cached-profile audit for {candidate.candidate_id}"
            )
    external_incumbent = None
    external_incumbent_raw_score = None
    external_incumbent_provenance = None
    if args.incumbent_manifest is not None:
        external_incumbent, external_incumbent_raw_score, external_incumbent_provenance = (
            load_truth_free_expanded_incumbent(
                args.incumbent_manifest,
                args.incumbent_rows,
                expected_static_hashes=static_hashes,
                active_config=config_data,
                expected_score=args.expected_incumbent_score,
            )
        )
        if external_incumbent.candidate_id in {item.candidate_id for item in candidates}:
            raise ValueError("expanded incumbent ID overlaps the frozen candidate sample")
        if external_incumbent.position_xy_m in {item.position_xy_m for item in candidates}:
            raise ValueError("expanded incumbent position overlaps the frozen candidate sample")
    measurements = read_contract(args.static / "measurements.json", MeasurementSetV2)
    catalog = read_contract(args.static / "catalog.json", CatalogV2)
    map_input = read_contract(args.static / "map.json", MapV2)
    config = SearchConfig(**config_data)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    setup_elapsed = time.perf_counter() - started
    activity_before = _activity()
    direct_start = time.perf_counter()
    exact_results = [
        (candidate.candidate_id, _exact(scorer, candidate.position_xy_m))
        for candidate in candidates
    ]
    exact_candidate_wall = time.perf_counter() - direct_start
    exact_by_id = dict(exact_results)
    external_incumbent_evaluation = None
    external_incumbent_validation_s = 0.0
    if external_incumbent is not None:
        assert external_incumbent_raw_score is not None
        external_start = time.perf_counter()
        external_incumbent_evaluation = _exact(scorer, external_incumbent.position_xy_m)
        external_incumbent_validation_s = time.perf_counter() - external_start
        if (
            external_incumbent_evaluation.status != "scored"
            or external_incumbent_evaluation.hypothesis is None
        ):
            raise ValueError(
                "expanded incumbent is not exactly scoreable by the frozen profile scorer"
            )
        if not math.isclose(
            external_incumbent_evaluation.hypothesis.score,
            external_incumbent_raw_score,
            abs_tol=ABS_TOL,
            rel_tol=REL_TOL,
        ):
            raise ValueError("expanded incumbent score differs under the frozen profile scorer")
    exact_wall = exact_candidate_wall + external_incumbent_validation_s
    for audit in a22_exact_audits:
        evaluation = exact_by_id[audit["candidate_id"]]
        direct_score = float(evaluation.hypothesis.score) if evaluation.hypothesis else None
        if evaluation.status != audit["status"] or (direct_score is None) != (
            audit["score"] is None
        ):
            raise ValueError("Direct scorer status differs from saved score artifact for candidate")
        if direct_score is not None and not math.isclose(
            direct_score, audit["score"], abs_tol=ABS_TOL, rel_tol=REL_TOL
        ):
            raise ValueError("Direct scorer score differs from saved score artifact for candidate")
    activity_at_screen = _activity()
    result: dict[str, Any] = {
        "status": "prepared_no_screening",
        "selected_count": len(candidates),
        "setup_elapsed_s": setup_elapsed,
        "direct_exact_wall_s": exact_wall,
        "direct_exact_candidate_wall_s": exact_candidate_wall,
        "direct_exact_incumbent_validation_s": external_incumbent_validation_s,
        "direct_exact_status_counts": dict(Counter(value.status for _, value in exact_results)),
        "direct_exact_audits": [
            {
                "candidate_id": candidate.candidate_id,
                "position_xy_m": list(candidate.position_xy_m),
                "status": evaluation.status,
                "score": evaluation.hypothesis.score if evaluation.hypothesis else None,
            }
            for candidate, (_, evaluation) in zip(candidates, exact_results, strict=True)
        ],
        "candidate_order_matches_score_only": True,
        "activity_before": activity_before,
        "activity_at_screen": activity_at_screen,
    }
    if args.run_screening:
        screen_start = time.perf_counter()
        screen_saved_incumbent_score = saved_incumbent_score
        if external_incumbent is not None:
            assert external_incumbent_raw_score is not None
            screen_saved_incumbent_score = external_incumbent_raw_score
        dispositions, screened_info = pre_los_screen(
            scorer,
            candidates,
            incumbent_id=incumbent_id if external_incumbent is None else None,
            saved_incumbent_score=screen_saved_incumbent_score,
            external_incumbent=external_incumbent,
        )
        screen_wall = time.perf_counter() - screen_start
        pruned = [item for item in dispositions if item.action == "proposed_prune"]
        exact_union_scores = [
            float(value.hypothesis.score)
            for value in exact_by_id.values()
            if value.status == "scored" and value.hypothesis is not None
        ]
        if external_incumbent_evaluation is not None:
            exact_union_scores.append(float(external_incumbent_evaluation.hypothesis.score))
        exact_union_best = min(exact_union_scores)
        validation, validation_elapsed = independently_validate_prunes(
            scorer, pruned, exact_best_score=exact_union_best
        )
        if len(dispositions) != len(candidates) or len(
            {item.candidate_id for item in dispositions}
        ) != len(candidates):
            raise AssertionError("candidate accounting contains omissions or duplicate IDs")
        if [item.candidate_id for item in dispositions] != [
            item.candidate_id for item in candidates
        ]:
            raise AssertionError(
                "disposition order differs from the complete input candidate stream"
            )
        if sum(item.action == "proposed_prune" for item in dispositions) + sum(
            item.action != "proposed_prune" for item in dispositions
        ) != len(candidates):
            raise AssertionError("exact and pruned accounting does not cover all candidates")
        if len({item.candidate_id for item in pruned}) != len(pruned):
            raise AssertionError("pruned candidate IDs are not unique")
        if any(
            not item.preparation_supported
            or not item.coverage_certified
            or not item.bound_supported
            or item.lower_bound is None
            for item in pruned
        ):
            raise AssertionError(
                "a pruned candidate lacks certified prepared targets and supported bound"
            )
        exact_by_id = dict(exact_results)
        exact_scores = {
            key: float(value.hypothesis.score)
            for key, value in exact_by_id.items()
            if value.status == "scored" and value.hypothesis is not None
        }
        exact_reference_scores = {
            item["candidate_id"]: item["score"]
            for item in exact_reference["audits"]
            if item["score"] is not None
        }
        if set(exact_scores) != set(exact_reference_scores) or any(
            not math.isclose(
                exact_scores[key], exact_reference_scores[key], abs_tol=ABS_TOL, rel_tol=REL_TOL
            )
            for key in exact_scores
        ):
            raise AssertionError(
                "current direct exact scores differ from saved score-only artifact"
            )
        screened_all_scores = {
            key: float(value.hypothesis.score)
            for key, value in screened_info["exact_results"].items()
            if value.status == "scored" and value.hypothesis is not None
        }
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        screened_scores = {
            key: value for key, value in screened_all_scores.items() if key in candidate_ids
        }
        exact_best = min(exact_scores.values())
        if not math.isclose(exact_best, a22_best_score, abs_tol=ABS_TOL, rel_tol=REL_TOL):
            raise AssertionError("current direct best differs from saved A22 exact artifact")
        exact_ties_from_a22 = [
            candidate_id
            for candidate_id, score in a22_scored_pairs
            if math.isclose(score, a22_best_score, abs_tol=ABS_TOL, rel_tol=REL_TOL)
        ]
        if exact_ties_from_a22 != exact_reference["tie_candidate_ids"]:
            raise AssertionError("A22 and cached-profile exact artifacts disagree on tie IDs")
        exact_ties = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.candidate_id in exact_scores
            and math.isclose(
                exact_scores[candidate.candidate_id], exact_best, abs_tol=ABS_TOL, rel_tol=REL_TOL
            )
        ]
        screened_best, screened_ties = _best_and_ties(
            screened_scores, [candidate.candidate_id for candidate in candidates]
        )
        sample_parity = (
            None
            if external_incumbent is not None
            else bool(
                screened_best is not None
                and math.isclose(exact_best, screened_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
                and exact_ties == screened_ties
                and screened_ties == exact_ties_from_a22
            )
        )
        exact_union_id_score = dict(exact_scores)
        if external_incumbent_evaluation is not None:
            assert external_incumbent is not None
            assert external_incumbent_evaluation.hypothesis is not None
            exact_union_id_score[external_incumbent.candidate_id] = float(
                external_incumbent_evaluation.hypothesis.score
            )
        screened_union_id_score = dict(screened_all_scores)
        exact_union_best = min(exact_union_id_score.values())
        screened_union_best = min(screened_union_id_score.values())
        exact_union_ties = sorted(
            candidate_id
            for candidate_id, score in exact_union_id_score.items()
            if math.isclose(score, exact_union_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
        )
        screened_union_ties = sorted(
            candidate_id
            for candidate_id, score in screened_union_id_score.items()
            if math.isclose(score, screened_union_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
        )
        union_parity = math.isclose(
            exact_union_best, screened_union_best, abs_tol=ABS_TOL, rel_tol=REL_TOL
        ) and set(exact_union_ties) == set(screened_union_ties)
        parity = (sample_parity is not False) and union_parity
        if not parity:
            raise AssertionError(
                {
                    "exact_best": exact_best,
                    "screened_best": screened_best,
                    "exact_ties": exact_ties,
                    "screened_ties": screened_ties,
                    "exact_union_best": exact_union_best,
                    "screened_union_best": screened_union_best,
                    "exact_union_ties": exact_union_ties,
                    "screened_union_ties": screened_union_ties,
                }
            )
        result.update(
            {
                "status": "screened_complete",
                "screened_wall_s": screen_wall,
                "independent_prune_validation_s": validation_elapsed,
                "pruned_count": len(pruned),
                "independent_pruned_score_checks": len(validation),
                "pruned_bounds_all_valid": True,
                "all_pruned_candidates_outside_exact_best_tie_set": True,
                "candidate_accounting_complete": len(dispositions) == len(candidates),
                "prune_ids_unique": len({item.candidate_id for item in pruned}) == len(pruned),
                "exact_action_count": sum(
                    item.action.startswith("exact_") for item in dispositions
                ),
                "unsupported_preparation_exact_fallback_count": sum(
                    item.action.startswith("exact_") and item.preparation_supported is False
                    for item in dispositions
                ),
                "unsupported_bound_exact_fallback_count": sum(
                    item.action.startswith("exact_") and item.bound_supported is False
                    for item in dispositions
                ),
                "unresolved_coverage_exact_fallback_count": sum(
                    item.exact_status == "unresolved_coverage" for item in dispositions
                ),
                "invalid_water_exact_status_count": sum(
                    item.exact_status == "invalid_water" for item in dispositions
                ),
                "best_score": screened_union_best,
                "tie_candidate_ids": screened_union_ties,
                "candidate_sample_best_score": screened_best,
                "candidate_sample_best_preserved": sample_parity,
                "candidate_sample_tie_candidate_ids": screened_ties,
                "saved_a22_exact_best_score": a22_best_score,
                "saved_a22_exact_tie_ids": exact_ties_from_a22,
                "score_and_tie_parity": parity,
                "sample_score_and_tie_parity": sample_parity,
                "union_score_and_tie_parity": union_parity,
                "exact_union_best_score": exact_union_best,
                "screened_union_best_score": screened_union_best,
                "exact_union_tie_candidate_ids": exact_union_ties,
                "screened_union_tie_candidate_ids": screened_union_ties,
                "incumbent_outside_candidate_set": external_incumbent is not None,
                "expanded_incumbent_source": external_incumbent_provenance,
                "expanded_incumbent_exact_revalidated_score": (
                    external_incumbent_evaluation.hypothesis.score
                    if external_incumbent_evaluation is not None
                    else None
                ),
                "screened_exact_status_counts": dict(
                    Counter(
                        item.exact_status for item in dispositions if item.exact_status is not None
                    )
                ),
                "screened_dispositions": [
                    item.__dict__
                    if hasattr(item, "__dict__")
                    else {field: getattr(item, field) for field in item.__dataclass_fields__}
                    for item in dispositions
                ],
                "screened_stage_seconds": screened_info["stage_seconds"],
            }
        )
    result.update(
        {
            "schema": "a22-pre-los-profile-v1",
            "started_utc": datetime.now(UTC).isoformat(),
            "source_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip(),
            "selection_sha256": selection_hash,
            "config_sha256": sha256_file(args.config),
            "input_sha256": static_hashes,
            "score_artifact_sha256": sha256_file(args.score_only),
            "profile_metadata_sha256": sha256_file(args.profile_metadata),
            "profile_source_commit": profile_metadata.get("scoring_source_sha"),
            "incumbent_candidate_id": (
                external_incumbent.candidate_id if external_incumbent is not None else incumbent_id
            ),
            "saved_incumbent_score": (
                external_incumbent_raw_score
                if external_incumbent is not None
                else saved_incumbent_score
            ),
            "active_scoring_config_projection_sha256": scoring_config_hash,
            "incumbent_exact_revalidated_before_screening": True,
            "incumbent_outside_candidate_set": external_incumbent is not None,
            "expanded_incumbent_source": external_incumbent_provenance,
            "thread_settings": {key: os.environ.get(key, "<unset>") for key in THREAD_KEYS},
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "no_truth_or_held_out_data": True,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output.resolve()),
                "selected": len(candidates),
                "direct_exact_wall_s": exact_wall,
                "direct_exact_status_counts": dict(
                    Counter(value.status for _, value in exact_results)
                ),
                "direct_exact_audits": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "position_xy_m": list(candidate.position_xy_m),
                        "status": evaluation.status,
                        "score": evaluation.hypothesis.score if evaluation.hypothesis else None,
                    }
                    for candidate, (_, evaluation) in zip(candidates, exact_results, strict=True)
                ],
                "screening_requested": args.run_screening,
            },
            sort_keys=True,
        )
    )
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
