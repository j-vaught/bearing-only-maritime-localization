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
    incumbent_id: str,
    saved_incumbent_score: float,
) -> tuple[tuple[CandidateDisposition, ...], dict[str, Any]]:
    """Experimental serial controller; every unsupported case uses exact scoring."""
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    seed = by_id.get(incumbent_id)
    if seed is None or not math.isfinite(saved_incumbent_score):
        raise ValueError("A saved finite exact incumbent in the candidate sample is required")
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
        raise ValueError("Saved incumbent is not exactly scoreable by current scorer")
    incumbent_score = float(seed_evaluation.hypothesis.score)
    if not math.isclose(incumbent_score, saved_incumbent_score, abs_tol=ABS_TOL, rel_tol=REL_TOL):
        raise ValueError("Revalidated incumbent score differs from score-only artifact")

    dispositions: list[CandidateDisposition] = []
    exact_results: dict[str, Any] = {incumbent_id: seed_evaluation}
    margins: list[float] = []
    for candidate in candidates:
        if candidate.candidate_id == incumbent_id:
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
            _isclose_tie_margin(
                incumbent_score,
                abs_tol=ABS_TOL,
                rel_tol=REL_TOL,
            ),
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
    return tuple(dispositions), {"stage_seconds": timing, "exact_results": exact_results}


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
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(".agent-work/benchmark.lock"),
        help="canonical benchmark lock; execution fails immediately if held",
    )
    parser.add_argument("--run-screening", action="store_true")
    args = parser.parse_args()
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
    exact_wall = time.perf_counter() - direct_start
    exact_by_id = dict(exact_results)
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
        dispositions, screened_info = pre_los_screen(
            scorer,
            candidates,
            incumbent_id=incumbent_id,
            saved_incumbent_score=saved_incumbent_score,
        )
        screen_wall = time.perf_counter() - screen_start
        pruned = [item for item in dispositions if item.action == "proposed_prune"]
        validation, validation_elapsed = independently_validate_prunes(
            scorer, pruned, exact_best_score=float(exact_reference["best_score"])
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
        screened_scores = {
            key: float(value.hypothesis.score)
            for key, value in screened_info["exact_results"].items()
            if value.status == "scored" and value.hypothesis is not None
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
        screened_best = min(screened_scores.values())
        exact_ties = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.candidate_id in exact_scores
            and math.isclose(
                exact_scores[candidate.candidate_id], exact_best, abs_tol=ABS_TOL, rel_tol=REL_TOL
            )
        ]
        screened_ties = [
            candidate.candidate_id
            for candidate in candidates
            if candidate.candidate_id in screened_scores
            and math.isclose(
                screened_scores[candidate.candidate_id],
                screened_best,
                abs_tol=ABS_TOL,
                rel_tol=REL_TOL,
            )
        ]
        parity = (
            math.isclose(exact_best, screened_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
            and exact_ties == screened_ties
            and screened_ties == exact_ties_from_a22
        )
        if not parity:
            raise AssertionError(
                {
                    "exact_best": exact_best,
                    "screened_best": screened_best,
                    "exact_ties": exact_ties,
                    "screened_ties": screened_ties,
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
                "unsupported_exact_fallback_count": sum(
                    item.action.startswith("exact_") and item.preparation_supported is False
                    for item in dispositions
                ),
                "best_score": screened_best,
                "tie_candidate_ids": screened_ties,
                "saved_a22_exact_best_score": a22_best_score,
                "saved_a22_exact_tie_ids": exact_ties_from_a22,
                "score_and_tie_parity": parity,
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
            "incumbent_candidate_id": incumbent_id,
            "saved_incumbent_score": saved_incumbent_score,
            "incumbent_exact_revalidated_before_screening": True,
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
