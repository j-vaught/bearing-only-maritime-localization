"""Compare exact and A22 serial screened scoring on the saved 255 candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.scoring import CandidateScorer
from bearing_localization.search.screened_search import (
    CandidatePosition,
    screened_candidate_search,
)
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogV2,
    MapV2,
    MeasurementSetV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

THREAD_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
ABS_TOL = 1e-8
REL_TOL = 1e-10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def activity() -> dict[str, Any]:
    result: dict[str, Any] = {"loadavg": None, "cpu_processes": [], "gpu_processes": []}
    try:
        result["loadavg"] = Path("/proc/loadavg").read_text().strip()
        proc = subprocess.run(
            ["ps", "-eo", "pid=,pcpu=,pmem=,comm="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        rows = []
        for line in proc.stdout.splitlines():
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
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        result["gpu_processes"] = proc.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    selection = json.loads(args.selected.read_text())
    candidates = tuple(
        CandidatePosition(candidate_id, tuple(position))
        for candidate_id, position in zip(
            selection["selected_ids"], selection["selected_positions_map_epsg32618"], strict=True
        )
    )
    measurements = read_contract(args.static / "measurements.json", MeasurementSetV2)
    catalog = read_contract(args.static / "catalog.json", CatalogV2)
    map_input = read_contract(args.static / "map.json", MapV2)
    config_payload = json.loads(args.config.read_text())
    config_payload.pop("contract_type", None)
    config_payload.pop("proposal_cache_path", None)
    config = SearchConfig(**config_payload)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    setup_elapsed = time.perf_counter() - started

    before = activity()
    exact_start = time.perf_counter()
    exact = [scorer.evaluate(candidate.position_xy_m) for candidate in candidates]
    exact_elapsed = time.perf_counter() - exact_start

    screened_start = time.perf_counter()
    screened = screened_candidate_search(
        scorer,
        candidates,
        visit_budget=len(candidates),
        complete_candidate_count=len(candidates),
    )
    screened_elapsed = time.perf_counter() - screened_start
    after = activity()

    exact_by_id = {
        candidate.candidate_id: result for candidate, result in zip(candidates, exact, strict=True)
    }
    exact_scores = {
        candidate_id: float(result.hypothesis.score)
        for candidate_id, result in exact_by_id.items()
        if result.status == "scored" and result.hypothesis is not None
    }
    actions = {visit.candidate_id: visit for visit in screened.visits}
    if tuple(actions) != tuple(candidate.candidate_id for candidate in candidates):
        raise AssertionError("screened result candidate order differs from selection")
    if set(actions) != set(exact_by_id):
        raise AssertionError("screened and exact candidate IDs differ")
    unsupported_prunes = []
    invalid_prunes = []
    for candidate_id, visit in actions.items():
        if visit.action == "pruned":
            if (
                visit.lower_bound is None
                or not visit.lower_bound.supported
                or visit.lower_bound.bound is None
            ):
                unsupported_prunes.append(candidate_id)
            elif (
                candidate_id not in exact_scores
                or visit.lower_bound.bound > exact_scores[candidate_id] + 1e-8
            ):
                invalid_prunes.append(candidate_id)
    if unsupported_prunes or invalid_prunes:
        raise AssertionError(
            {"unsupported_prunes": unsupported_prunes, "invalid_prunes": invalid_prunes}
        )
    exact_best = min(exact_scores.values()) if exact_scores else None
    screened_best = screened.best_scored_candidate.score if screened.best_scored_candidate else None
    exact_ties = [
        key
        for key, score in exact_scores.items()
        if exact_best is not None
        and math.isclose(score, exact_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
    ]
    position_to_id = {candidate.position_xy_m: candidate.candidate_id for candidate in candidates}
    screened_ties = [
        position_to_id[(item.east_m, item.north_m)]
        for item in screened.hypotheses
        if screened_best is not None
        and math.isclose(item.score, screened_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
    ]
    parity = (
        exact_best is not None
        and screened_best is not None
        and math.isclose(exact_best, screened_best, abs_tol=ABS_TOL, rel_tol=REL_TOL)
        and exact_ties == screened_ties
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

    result = {
        "schema": "a22-same255-profile-v1",
        "started_utc": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "selected_manifest_sha256": sha256(args.selected),
        "config_sha256": sha256(args.config),
        "effective_config": {key: value for key, value in config_payload.items()},
        "selected_count": len(candidates),
        "selected_ids": [candidate.candidate_id for candidate in candidates],
        "selected_positions_map_epsg32618": [
            list(candidate.position_xy_m) for candidate in candidates
        ],
        "setup_elapsed_s": setup_elapsed,
        "exact_wall_s": exact_elapsed,
        "screened_wall_s": screened_elapsed,
        "exact_status_counts": {
            status: sum(item.status == status for item in exact)
            for status in sorted({item.status for item in exact})
        },
        "screened_counts": {
            "candidate_count": screened.candidate_count,
            "exact_evaluation_count": screened.exact_evaluation_count,
            "exact_scored_count": screened.exact_scored_count,
            "pruned_count": screened.pruned_count,
            "invalid_water_count": screened.invalid_water_count,
            "unresolved_count": screened.unresolved_count,
        },
        "best_score": exact_best,
        "tie_candidate_ids": exact_ties,
        "parity": {"best_score": parity, "best_ties": parity},
        "pruned_bounds_valid_against_independent_exact_scores": True,
        "unsupported_prune_count": len(unsupported_prunes),
        "invalid_prune_count": len(invalid_prunes),
        "thread_settings": {key: os.environ.get(key, "<unset>") for key in THREAD_KEYS},
        "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "activity_before": before,
        "activity_after": after,
        "exact_audits": [
            {
                "candidate_id": candidate.candidate_id,
                "status": evaluation.status,
                "score": evaluation.hypothesis.score if evaluation.hypothesis else None,
            }
            for candidate, evaluation in zip(candidates, exact, strict=True)
        ],
        "screened_audits": [
            {
                "candidate_id": visit.candidate_id,
                "action": visit.action,
                "status": visit.evaluation_status,
                "score": visit.score,
                "lower_bound": visit.lower_bound.bound if visit.lower_bound else None,
                "bound_supported": visit.lower_bound.supported if visit.lower_bound else None,
            }
            for visit in screened.visits
        ],
        "no_truth_or_held_out_data": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str(args.output.resolve()),
                "exact_wall_s": exact_elapsed,
                "screened_wall_s": screened_elapsed,
                "pruned_count": screened.pruned_count,
                "parity": parity,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
