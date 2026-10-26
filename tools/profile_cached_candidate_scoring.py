"""Profile exact and screened scoring on a fixed proposal-cache sample."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import subprocess
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from bearing_localization.search.config_serialization import read_search_config
from bearing_localization.search.parallel_screened import (
    CandidateAudit,
    ParallelScreenedCandidatePool,
    ParallelScreenedError,
)
from bearing_localization.search.proposals.cache import ProposalCacheContext, load_proposal_cache
from bearing_localization.search.proposals.geodesic_wedge import GeodesicWedgeProposal
from bearing_localization.search.screened_search import CandidatePosition
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.geospatial.coordinates import LocalFrame
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

DEFAULT_OUTPUT = Path("reports/experiments/cached-candidate-scoring")
DEFAULT_CACHE = Path(".agent-work/outputs/a26-screened-cache-diagnostic-v4/cache/proposals-v1")
DEFAULT_STATIC = Path(".agent-work/outputs/a26-screened-cache-diagnostic-v4/static-inputs")
DEFAULT_CONFIG = Path(
    ".agent-work/outputs/a26-screened-cache-diagnostic-v4/cache-build/search-config.json"
)
EXPECTED_TREE_SHA256 = "d6154c18349842d22ca1cd5eaf2d022a6ac67004eebc9e35c5c59d03ad767e72"
EXPECTED_CONTEXT_SHA256 = "3e450d04fd82d4bec5c5786d9e49960d51a0dab4615e7237b55bc493251aea44"
EXPECTED_INPUT_SHA256 = {
    "measurements.json": "ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1",
    "catalog.json": "2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704",
    "map.json": "cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93",
    "scope-manifest.json": "d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1",
}
ABS_TOL = 1e-8
REL_TOL = 1e-10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = item.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def fixed_sample_indices(
    total: int, *, prefix: int = 128, evenly_spaced: int = 128
) -> tuple[int, ...]:
    """Select cache-order prefix plus evenly spaced indices with stable deduplication."""
    if type(total) is not int or total < 1:
        raise ValueError("total must be a positive integer")
    if type(prefix) is not int or prefix < 0 or type(evenly_spaced) is not int or evenly_spaced < 0:
        raise ValueError("sample counts must be nonnegative integers")
    selected: list[int] = []
    seen: set[int] = set()
    for index in list(range(min(prefix, total))) + list(
        np.linspace(0, total - 1, min(evenly_spaced, total), dtype=np.int64)
    ):
        value = int(index)
        if value not in seen:
            selected.append(value)
            seen.add(value)
    return tuple(selected)


def evenly_spaced_indices(total: int, count: int = 2048) -> tuple[int, ...]:
    """Return a fixed cache-index sample without a prefix stratum."""
    if type(total) is not int or total < 1:
        raise ValueError("total must be a positive integer")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    values = np.linspace(0, total - 1, min(total, count), dtype=np.int64)
    return tuple(dict.fromkeys(int(value) for value in values))


def best_score_and_ties(audits: Sequence[CandidateAudit]) -> tuple[float | None, tuple[str, ...]]:
    scored = [(item.candidate_id, float(item.score)) for item in audits if item.score is not None]
    if not scored:
        return None, ()
    best = min(score for _, score in scored)
    ties = tuple(
        candidate_id
        for candidate_id, score in scored
        if np.isclose(score, best, atol=ABS_TOL, rtol=REL_TOL)
    )
    return best, ties


def compare_best(left: dict[str, Any], right: dict[str, Any]) -> None:
    left_score, right_score = left["best_score"], right["best_score"]
    if left_score is None or right_score is None:
        if left_score != right_score:
            raise AssertionError(f"best score presence differs: {left_score!r} vs {right_score!r}")
    elif not np.isclose(left_score, right_score, atol=ABS_TOL, rtol=REL_TOL):
        raise AssertionError(f"best score mismatch: {left_score!r} vs {right_score!r}")
    if tuple(left["tie_candidate_ids"]) != tuple(right["tie_candidate_ids"]):
        raise AssertionError("best tie IDs mismatch")


def _audit_json(audit: CandidateAudit) -> dict[str, Any]:
    return {
        "candidate_id": audit.candidate_id,
        "input_index": audit.input_index,
        "position_xy_m": list(audit.position_xy_m),
        "action": audit.action,
        "score": audit.score,
        "lower_bound": audit.lower_bound,
        "lower_bound_supported": audit.lower_bound_supported,
        "visible_group_count_k": audit.visible_group_count_k,
        "model_status": audit.model_status,
    }


def _activity_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {"loadavg": None, "cpu_processes": [], "gpu_processes": []}
    try:
        result["loadavg"] = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
        output = subprocess.run(
            ["ps", "-eo", "pid=,pcpu=,pmem=,comm="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        rows = []
        for line in output.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4 and float(parts[1]) > 1.0:
                rows.append(
                    {
                        "pid": int(parts[0]),
                        "pcpu": float(parts[1]),
                        "pmem": float(parts[2]),
                        "comm": parts[3],
                    }
                )
        result["cpu_processes"] = rows[:32]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        result["gpu_processes"] = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    return result


@contextmanager
def benchmark_lock(path: Path) -> Generator[None, None, None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _proposal_position(
    proposal: GeodesicWedgeProposal, frame: LocalFrame, to_map: Transformer
) -> tuple[float, float]:
    lonlat = frame.to_geographic(proposal.east_m, proposal.north_m)
    east, north = to_map.transform(*lonlat)
    return float(east), float(north)


def run_case(
    *,
    measurements: MeasurementSetV2,
    config: Any,
    catalog: CatalogV2,
    vector_map: VectorMap,
    candidates: Sequence[CandidatePosition],
    workers: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    case_results: dict[str, Any] = {}
    for mode in ("exact", "screened"):
        case_start = time.perf_counter()
        audits: list[CandidateAudit] = []
        chunk_rows: list[dict[str, Any]] = []
        with ParallelScreenedCandidatePool(
            catalog,
            vector_map,
            workers=workers,
            memory_budget_bytes=16 * 1024**3,
            chunk_size=32,
            max_in_flight_chunks=2 * workers,
            request_timeout_s=570.0,
        ) as pool:
            for chunk in pool.iter_chunks(
                measurements,
                config,
                candidates,
                mode=mode,
                complete_candidate_count=len(candidates),
            ):
                audits.extend(chunk.candidates)
                chunk_rows.append(
                    {
                        "chunk_index": chunk.chunk_index,
                        "candidate_count": chunk.candidate_count,
                        "exact_scored_count": chunk.exact_scored_count,
                        "exact_evaluation_count": chunk.exact_evaluation_count,
                        "invalid_water_count": chunk.invalid_water_count,
                        "unresolved_count": chunk.unresolved_count,
                        "pruned_count": chunk.pruned_count,
                        "incumbent_seed_evaluation_count": chunk.incumbent_seed_evaluation_count,
                        "incumbent_validation_count": chunk.incumbent_validation_count,
                        "screened_elapsed_s": chunk.screened_elapsed_s,
                        "exact_elapsed_s": chunk.exact_elapsed_s,
                        "worker_evaluation_s": chunk.worker_evaluation_s,
                        "estimated_memory_bytes": chunk.estimated_memory_bytes,
                        "worker_rss_bytes": [list(item) for item in chunk.worker_rss_bytes],
                    }
                )
            timings = pool.last_timings
        if [audit.candidate_id for audit in audits] != [
            candidate.candidate_id for candidate in candidates
        ]:
            raise AssertionError(f"{mode} candidate order changed")
        unsupported_prunes = [
            audit.candidate_id
            for audit in audits
            if audit.action == "pruned" and audit.lower_bound_supported is not True
        ]
        if unsupported_prunes:
            raise AssertionError(
                f"pruned candidates lack supported bounds: {unsupported_prunes[:3]}"
            )
        best_score, tie_ids = best_score_and_ties(audits)
        case_results[mode] = {
            "workers": workers,
            "mode": mode,
            "elapsed_s": time.perf_counter() - case_start,
            "best_score": best_score,
            "tie_candidate_ids": list(tie_ids),
            "candidate_count": len(audits),
            "exact_scored_count": sum(item["exact_scored_count"] for item in chunk_rows),
            "exact_evaluation_count": sum(item["exact_evaluation_count"] for item in chunk_rows),
            "invalid_water_count": sum(item["invalid_water_count"] for item in chunk_rows),
            "unresolved_count": sum(item["unresolved_count"] for item in chunk_rows),
            "pruned_count": sum(item["pruned_count"] for item in chunk_rows),
            "incumbent_seed_evaluation_count": sum(
                item["incumbent_seed_evaluation_count"] for item in chunk_rows
            ),
            "incumbent_validation_count": sum(
                item["incumbent_validation_count"] for item in chunk_rows
            ),
            "chunk_screened_elapsed_s": sum(item["screened_elapsed_s"] for item in chunk_rows),
            "chunk_exact_elapsed_s": sum(item["exact_elapsed_s"] for item in chunk_rows),
            "worker_evaluation_s": timings.worker_evaluation_s,
            "pool_startup_s": timings.pool_startup_s,
            "scorer_setup_s": timings.scorer_setup_s,
            "ipc_s": timings.ipc_s,
            "pool_elapsed_s": timings.elapsed_s,
            "estimated_memory_bytes": timings.estimated_memory_bytes,
            "worker_rss_bytes": [list(item) for item in timings.worker_rss_bytes],
            "audits": [_audit_json(audit) for audit in audits],
            "chunks": chunk_rows,
        }
    compare_best(case_results["exact"], case_results["screened"])
    case_results["parity"] = {"best_objective": True, "best_ties": True}
    case_results["case_elapsed_s"] = time.perf_counter() - started
    return case_results


def run_exact_scaling_case(
    *,
    measurements: MeasurementSetV2,
    config: Any,
    catalog: CatalogV2,
    vector_map: VectorMap,
    candidates: Sequence[CandidatePosition],
    workers: int,
) -> dict[str, Any]:
    """Run one exact request and retain disposition details on timeout."""
    started = time.perf_counter()
    audits: list[CandidateAudit] = []
    chunks: list[dict[str, Any]] = []
    pool: ParallelScreenedCandidatePool | None = None
    try:
        with ParallelScreenedCandidatePool(
            catalog,
            vector_map,
            workers=workers,
            memory_budget_bytes=32 * 1024**3,
            chunk_size=32,
            max_in_flight_chunks=2 * workers,
            request_timeout_s=540.0,
        ) as pool:
            for chunk in pool.iter_chunks(
                measurements,
                config,
                candidates,
                mode="exact",
                complete_candidate_count=len(candidates),
            ):
                audits.extend(chunk.candidates)
                chunks.append(
                    {
                        "chunk_index": chunk.chunk_index,
                        "candidate_count": chunk.candidate_count,
                        "exact_scored_count": chunk.exact_scored_count,
                        "exact_evaluation_count": chunk.exact_evaluation_count,
                        "invalid_water_count": chunk.invalid_water_count,
                        "unresolved_count": chunk.unresolved_count,
                        "worker_evaluation_s": chunk.worker_evaluation_s,
                        "exact_elapsed_s": chunk.exact_elapsed_s,
                        "worker_rss_bytes": [list(item) for item in chunk.worker_rss_bytes],
                    }
                )
        timings = pool.last_timings
        if [audit.candidate_id for audit in audits] != [
            candidate.candidate_id for candidate in candidates
        ]:
            raise AssertionError("exact scaling candidate order changed")
        best_score, tie_ids = best_score_and_ties(audits)
        return {
            "status": "completed",
            "workers": workers,
            "mode": "exact",
            "candidate_count": len(audits),
            "elapsed_s": time.perf_counter() - started,
            "best_score": best_score,
            "tie_candidate_ids": list(tie_ids),
            "exact_scored_count": sum(item["exact_scored_count"] for item in chunks),
            "exact_evaluation_count": sum(item["exact_evaluation_count"] for item in chunks),
            "invalid_water_count": sum(item["invalid_water_count"] for item in chunks),
            "unresolved_count": sum(item["unresolved_count"] for item in chunks),
            "pool_startup_s": timings.pool_startup_s,
            "scorer_setup_s": timings.scorer_setup_s,
            "ipc_s": timings.ipc_s,
            "pool_elapsed_s": timings.elapsed_s,
            "worker_evaluation_s": timings.worker_evaluation_s,
            "estimated_memory_bytes": timings.estimated_memory_bytes,
            "worker_rss_bytes": [list(item) for item in timings.worker_rss_bytes],
            "audits": [_audit_json(audit) for audit in audits],
            "chunks": chunks,
        }
    except ParallelScreenedError as exc:
        timings = pool.last_timings if pool is not None else None
        disposition = exc.disposition
        return {
            "status": "timeout_or_failure",
            "workers": workers,
            "mode": "exact",
            "candidate_count": len(audits),
            "elapsed_s": time.perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
            "disposition": {
                "consumed_count": len(disposition.consumed_candidate_ids),
                "submitted_count": len(disposition.submitted_candidate_ids),
                "committed_count": len(disposition.committed_candidate_ids),
                "pending_count": len(disposition.pending_candidate_ids),
                "failed_count": len(disposition.failed_candidate_ids),
                "outstanding_count": len(disposition.outstanding_candidate_ids),
                "not_dispatched_count": len(disposition.not_dispatched_candidate_ids),
            },
            "pool_startup_s": timings.pool_startup_s if timings else None,
            "scorer_setup_s": timings.scorer_setup_s if timings else None,
            "ipc_s": timings.ipc_s if timings else None,
            "pool_elapsed_s": timings.elapsed_s if timings else None,
            "worker_evaluation_s": timings.worker_evaluation_s if timings else None,
            "estimated_memory_bytes": timings.estimated_memory_bytes if timings else None,
            "worker_rss_bytes": [list(item) for item in timings.worker_rss_bytes]
            if timings
            else [],
            "audits": [_audit_json(audit) for audit in audits],
            "chunks": chunks,
        }


def validate_inputs(
    cache_path: Path, static_dir: Path, config_path: Path
) -> tuple[MeasurementSetV2, CatalogV2, Any, Any, dict[str, str]]:
    metadata = json.loads((cache_path / "metadata.json").read_text(encoding="utf-8"))
    core = metadata["metadata"]
    if core["context_sha256"] != EXPECTED_CONTEXT_SHA256:
        raise ValueError(f"proposal cache context mismatch: {core['context_sha256']}")
    if tree_sha256(cache_path) != EXPECTED_TREE_SHA256:
        raise ValueError("proposal cache tree hash mismatch")
    input_hashes = {name: sha256_file(static_dir / name) for name in EXPECTED_INPUT_SHA256}
    if input_hashes != EXPECTED_INPUT_SHA256:
        raise ValueError(f"frozen input hashes mismatch: {input_hashes}")
    measurements = read_contract(static_dir / "measurements.json", MeasurementSetV2)
    catalog = read_contract(static_dir / "catalog.json", CatalogV2)
    map_input = read_contract(static_dir / "map.json", MapV2)
    config = read_search_config(config_path)
    if config.proposal_cache_path != str(cache_path.resolve()):
        raise ValueError("SearchConfig proposal cache path is not the frozen cache")
    proposals = load_proposal_cache(
        cache_path, context=ProposalCacheContext(**core["context_manifest"])
    )
    if len(proposals.proposals) != 1_457_546:
        raise ValueError(f"unexpected proposal count {len(proposals.proposals)}")
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    return measurements, catalog, config, (proposals, vector_map), input_hashes


def profile(
    *, cache_path: Path, static_dir: Path, config_path: Path, output_dir: Path, lock_path: Path
) -> dict[str, Any]:
    measurements, catalog, config, (proposals, vector_map), input_hashes = validate_inputs(
        cache_path, static_dir, config_path
    )
    indices = fixed_sample_indices(len(proposals.proposals))
    selected = [proposals.proposals[index] for index in indices]
    frame = LocalFrame(proposals.frame_origin_lonlat, proposals.frame_crs)
    to_map = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    candidates = tuple(
        CandidatePosition(proposal.candidate_id, _proposal_position(proposal, frame, to_map))
        for proposal in selected
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = {
        "schema": "cached-candidate-scoring-selection-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "sampling": (
            "first 128 cache indices plus 128 evenly spaced indices across full cache, "
            "stable deduplication"
        ),
        "cache_order_sampling": True,
        "cache_tree_sha256": tree_sha256(cache_path),
        "cache_context_sha256": EXPECTED_CONTEXT_SHA256,
        "cache_candidate_count": len(proposals.proposals),
        "selected_count": len(selected),
        "selected_indices": list(indices),
        "selected_ids": [proposal.candidate_id for proposal in selected],
        "selected_positions_map_epsg32618": [
            list(candidate.position_xy_m) for candidate in candidates
        ],
        "input_sha256": input_hashes,
    }
    selection_path = output_dir / "selected-candidates.json"
    if selection_path.exists() and json.loads(selection_path.read_text()) != selection:
        raise FileExistsError(f"refusing to overwrite changed selection manifest: {selection_path}")
    selection_path.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    activity_before = _activity_snapshot()
    profile_started = datetime.now(UTC).isoformat()
    with benchmark_lock(lock_path):
        activity_locked = _activity_snapshot()
        cases = {}
        for workers in (1, 4):
            cases[str(workers)] = run_case(
                measurements=measurements,
                config=config,
                catalog=catalog,
                vector_map=vector_map,
                candidates=candidates,
                workers=workers,
            )
    compare_best(cases["1"]["exact"], cases["4"]["exact"])
    compare_best(cases["1"]["screened"], cases["4"]["screened"])
    result = {
        "schema": "cached-candidate-scoring-profile-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "profile_started_utc": profile_started,
        "sampling": selection["sampling"],
        "cache_order_sampling": True,
        "cache_tree_sha256": EXPECTED_TREE_SHA256,
        "cache_context_sha256": EXPECTED_CONTEXT_SHA256,
        "cache_candidate_count": len(proposals.proposals),
        "selected_count": len(selected),
        "selected_manifest": str(selection_path),
        "selected_ids_recorded_before_measurements": True,
        "input_sha256": input_hashes,
        "catalog_record_count": len(catalog.records),
        "measurement_count": len(measurements.measurements),
        "map_target_crs": "EPSG:32618",
        "proposal_frame_crs": proposals.frame_crs,
        "proposal_frame_origin_lonlat": list(proposals.frame_origin_lonlat),
        "config_path": str(config_path.resolve()),
        "config": {
            "algorithm": config.algorithm,
            "objective_version": config.objective_version,
            "max_range_m": config.max_range_m,
            "use_types": config.use_types,
            "candidate_budget": config.candidate_budget,
        },
        "benchmark_lock": str(lock_path.resolve()),
        "activity_before_lock": activity_before,
        "activity_at_lock": activity_locked,
        "activity_classification": (
            "Shared-host CPU/GPU activity was sampled for diagnostics; timings are "
            "diagnostic if other work was active."
        ),
        "cases": cases,
        "no_truth_or_held_out_data": True,
        "watchdog_limit_s": 600,
    }
    (output_dir / "profile-results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return result


def profile_evenly_spaced_scaling(
    *,
    cache_path: Path,
    static_dir: Path,
    config_path: Path,
    output_dir: Path,
    lock_path: Path,
    baseline_result: Path,
    scoring_source_sha: str,
) -> dict[str, Any]:
    """Profile exact scoring for 2,048 evenly spaced cache indices."""
    measurements, catalog, config, (proposals, vector_map), input_hashes = validate_inputs(
        cache_path, static_dir, config_path
    )
    indices = evenly_spaced_indices(len(proposals.proposals), 2048)
    selected = [proposals.proposals[index] for index in indices]
    frame = LocalFrame(proposals.frame_origin_lonlat, proposals.frame_crs)
    to_map = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    candidates = tuple(
        CandidatePosition(proposal.candidate_id, _proposal_position(proposal, frame, to_map))
        for proposal in selected
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = {
        "schema": "cached-candidate-scaling-selection-v1",
        "sampling": "2,048 evenly spaced cache indices across the full cache",
        "cache_order_sampling": True,
        "cache_tree_sha256": EXPECTED_TREE_SHA256,
        "cache_context_sha256": EXPECTED_CONTEXT_SHA256,
        "cache_candidate_count": len(proposals.proposals),
        "selected_count": len(selected),
        "selected_indices": list(indices),
        "selected_ids": [proposal.candidate_id for proposal in selected],
        "selected_positions_map_epsg32618": [
            list(candidate.position_xy_m) for candidate in candidates
        ],
        "input_sha256": input_hashes,
    }
    selection_path = output_dir / "scaling-2048-selection.json"
    if selection_path.exists():
        if json.loads(selection_path.read_text()) != selection:
            raise FileExistsError(
                f"refusing to overwrite changed selection manifest: {selection_path}"
            )
    else:
        selection_path.write_text(
            json.dumps(selection, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
        )
    activity_before = _activity_snapshot()
    profile_started = datetime.now(UTC).isoformat()
    cases: dict[str, Any] = {}
    with benchmark_lock(lock_path):
        activity_locked = _activity_snapshot()
        for workers in (1, 4, 8, 16):
            cases[str(workers)] = run_exact_scaling_case(
                measurements=measurements,
                config=config,
                catalog=catalog,
                vector_map=vector_map,
                candidates=candidates,
                workers=workers,
            )
    completed = [case for case in cases.values() if case["status"] == "completed"]
    for left_index, left in enumerate(completed):
        for right in completed[left_index + 1 :]:
            compare_best(left, right)
    baseline = json.loads(baseline_result.read_text(encoding="utf-8"))
    baseline_exact = baseline["cases"]["1"]["exact"]
    baseline_ties = tuple(baseline_exact["tie_candidate_ids"])
    selected_ids = set(selection["selected_ids"])
    result = {
        "schema": "cached-candidate-scaling-profile-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "profile_started_utc": profile_started,
        "sampling": selection["sampling"],
        "cache_order_sampling": True,
        "cache_tree_sha256": EXPECTED_TREE_SHA256,
        "cache_context_sha256": EXPECTED_CONTEXT_SHA256,
        "cache_candidate_count": len(proposals.proposals),
        "selected_count": len(selected),
        "selected_manifest": str(selection_path),
        "selected_ids_recorded_before_measurements": True,
        "input_sha256": input_hashes,
        "catalog_record_count": len(catalog.records),
        "measurement_count": len(measurements.measurements),
        "map_target_crs": "EPSG:32618",
        "proposal_frame_crs": proposals.frame_crs,
        "proposal_frame_origin_lonlat": list(proposals.frame_origin_lonlat),
        "config_path": str(config_path.resolve()),
        "config": {
            "algorithm": config.algorithm,
            "objective_version": config.objective_version,
            "max_range_m": config.max_range_m,
            "use_types": config.use_types,
            "candidate_budget": config.candidate_budget,
        },
        "scoring_source_sha": scoring_source_sha,
        "harness_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path.cwd(), text=True
        ).strip(),
        "baseline_result_sha256": sha256_file(baseline_result),
        "comparison_to_previous_profile": {
            "baseline_best_score": baseline_exact["best_score"],
            "baseline_best_tie_ids": list(baseline_ties),
            "baseline_tie_ids_in_even_sample": sorted(set(baseline_ties) & selected_ids),
            "sample_best_scores_are_not_full_domain_objectives": True,
        },
        "benchmark_lock": str(lock_path.resolve()),
        "activity_before_lock": activity_before,
        "activity_at_lock": activity_locked,
        "activity_classification": (
            "Shared-host CPU/GPU activity was sampled for diagnostics; timings are "
            "diagnostic if other work was active."
        ),
        "cases": cases,
        "no_truth_or_held_out_data": True,
        "watchdog_limit_s": 600,
    }
    (output_dir / "scaling-2048-results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + chr(10), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--static-input-dir", type=Path, default=DEFAULT_STATIC)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scaling-2048", action="store_true")
    parser.add_argument(
        "--scaling-output-dir",
        type=Path,
        default=Path("reports/experiments/cached-candidate-scaling"),
    )
    parser.add_argument("--baseline-result", type=Path)
    parser.add_argument("--scoring-source-sha", default="0d34a17")
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    cache = args.cache.resolve()
    lock = args.lock or cache.parents[3] / ".agent-work/benchmark.lock"
    if args.scaling_2048:
        baseline = args.baseline_result or (args.output_dir / "profile-results.json")
        result = profile_evenly_spaced_scaling(
            cache_path=cache,
            static_dir=args.static_input_dir.resolve(),
            config_path=args.config.resolve(),
            output_dir=args.scaling_output_dir.resolve(),
            lock_path=lock.resolve(),
            baseline_result=baseline.resolve(),
            scoring_source_sha=args.scoring_source_sha,
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "output": str(
                        (args.scaling_output_dir / "scaling-2048-results.json").resolve()
                    ),
                    "selected_count": result["selected_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    result = profile(
        cache_path=cache,
        static_dir=args.static_input_dir.resolve(),
        config_path=args.config.resolve(),
        output_dir=args.output_dir.resolve(),
        lock_path=lock.resolve(),
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str((args.output_dir / "profile-results.json").resolve()),
                "selected_count": result["selected_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
