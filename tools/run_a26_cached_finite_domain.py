"""Score every cached geodesic proposal with ordered, durable chunk checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

EXPECTED_SEARCH_SHA = "818d4c714c28c978b64851836a4a968116f8d409"
EXPECTED_CACHE_FORMAT = "geodesic-proposal-cache-v1"
DEFAULT_TIMEOUT_S = 1800.0
DEFAULT_WORKERS = 4
DEFAULT_MEMORY_BUDGET_BYTES = 64 * 1024**3
DEFAULT_CHUNK_SIZE = 64


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(path: Path) -> str:
    h = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        name = item.relative_to(path).as_posix().encode()
        h.update(len(name).to_bytes(8, "big"))
        h.update(name)
        h.update(bytes.fromhex(sha256_file(item)))
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def append_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()


def validate_generation_provenance(
    *, search_root: Path, run_root: Path, static_root: Path, cache_path: Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Validate raw input, telemetry, implementation, and cache bindings."""
    sys.path.insert(0, str(search_root.resolve()))
    from bearing_localization import accuracy_cli
    from bearing_localization.search.proposals.cache import ProposalCacheContext

    config_hash = getattr(accuracy_cli, "_proposal_generation_config_sha256")
    implementation_hash = getattr(accuracy_cli, "_proposal_generation_implementation_sha256")

    manifest_path = run_root / "run-manifest.json"
    telemetry_path = run_root / "estimate.telemetry.json"
    if not manifest_path.is_file() or not telemetry_path.is_file():
        raise FileNotFoundError("Completed cache generation manifest and telemetry are required.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    if manifest.get("process_result", {}).get("status") != "completed":
        raise ValueError("Cache generation did not complete successfully.")
    if manifest.get("search_source_sha") != EXPECTED_SEARCH_SHA:
        raise ValueError("Cache generation source SHA does not match the pinned source.")
    if manifest.get("telemetry_sha256") != sha256_file(telemetry_path):
        raise ValueError("Cache generation telemetry hash mismatch.")
    if manifest.get("cache_path") != str(cache_path.resolve()):
        raise ValueError("Manifest cache path does not match the requested cache.")
    if manifest.get("cache_tree_sha256") != sha256_tree(cache_path):
        raise ValueError("Cache tree hash does not match the generation manifest.")
    static_paths = {
        "measurements": static_root / "measurements.json",
        "catalog": static_root / "catalog.json",
        "map": static_root / "map.json",
    }
    context = telemetry.get("diagnostics", {}).get("proposal_cache", {})
    if context.get("action") != "generated_and_saved":
        raise ValueError("Telemetry does not record a completed fresh cache generation.")
    if context.get("cache_format") != EXPECTED_CACHE_FORMAT:
        raise ValueError("Unexpected proposal cache format.")
    hashes = {name: sha256_file(path) for name, path in static_paths.items()}
    declared_hashes = manifest.get("input_sha256", {})
    for name, value in hashes.items():
        if declared_hashes.get(name) != value:
            raise ValueError(f"Generation manifest does not bind frozen {name} input.")
    raw_context = context.get("context")
    if not isinstance(raw_context, dict):
        raise ValueError("Cache context is missing from telemetry.")
    expected_context_keys = {
        "measurement_sha256": hashes["measurements"],
        "catalog_sha256": hashes["catalog"],
        "map_sha256": hashes["map"],
    }
    for key, value in expected_context_keys.items():
        if raw_context.get(key) != value:
            raise ValueError(f"Cache context does not bind frozen {key}.")
    generation_config = context.get("generation_config")
    if not isinstance(generation_config, dict):
        raise ValueError("Generation settings are missing from cache telemetry.")
    if raw_context.get("search_config_sha256") != config_hash(generation_config):
        raise ValueError("Cache generation configuration digest mismatch.")
    implementation_sha = implementation_hash(search_root)
    if raw_context.get("implementation_sha256") != implementation_sha:
        raise ValueError("Cache generation implementation digest mismatch.")
    verified_context = ProposalCacheContext(**raw_context)
    if context.get("context_sha256") != verified_context.context_hash:
        raise ValueError("Cache context SHA-256 mismatch.")
    return manifest, static_paths


def _candidate_records(cache_result: Any, vector_map: Any) -> tuple[list[dict[str, Any]], float]:
    """Apply the CLI's full deterministic proposal order, including map status."""
    from pyproj import Transformer

    from bearing_localization.accuracy_cli import _order_geodesic_proposals_for_scoring
    from maritime_localization_shared.geospatial.coordinates import LocalFrame

    started = time.perf_counter()
    frame = LocalFrame(cache_result.frame_origin_lonlat, cache_result.frame_crs)
    to_map = Transformer.from_crs("EPSG:4326", vector_map.crs, always_xy=True)
    positions: list[tuple[float, float]] = []
    for proposal in cache_result.proposals:
        lon, lat = frame.to_geographic(proposal.east_m, proposal.north_m)
        x, y = to_map.transform(lon, lat)
        positions.append((float(x), float(y)))
    order = _order_geodesic_proposals_for_scoring(
        cache_result.proposals,
        positions,
        water_status=vector_map.point_water_status,
    )
    records = []
    for index in order:
        proposal = cache_result.proposals[index]
        records.append(
            {
                "candidate_id": f"cell:{proposal.candidate_id}",
                "cache_index": index,
                "cell_i": proposal.cell_i,
                "cell_j": proposal.cell_j,
                "position_xy_m": positions[index],
                "matching_count": proposal.matching_count,
                "vote_count": proposal.vote_count,
                "normalized_angular_residual": proposal.normalized_angular_residual,
                "evidence_score": proposal.evidence_score,
            }
        )
    return records, time.perf_counter() - started


def run_finite_domain(
    *,
    search_root: Path,
    static_root: Path,
    cache_path: Path,
    generation_run_root: Path,
    output_root: Path,
    workers: int = DEFAULT_WORKERS,
    memory_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Load a verified cache and exact-score its entire ordered candidate domain."""
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Fresh finite-domain output directory required: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if workers < 1 or chunk_size < 1 or memory_budget_bytes < 1 or timeout_s <= 0:
        raise ValueError("workers, chunk size, memory budget, and timeout must be positive.")
    wall_start = time.monotonic()
    stages: dict[str, float] = {}
    provenance_start = time.perf_counter()
    generation_manifest, static_paths = validate_generation_provenance(
        search_root=search_root,
        run_root=generation_run_root,
        static_root=static_root,
        cache_path=cache_path,
    )
    stages["generation_provenance_validation_s"] = time.perf_counter() - provenance_start
    sys.path.insert(0, str(search_root.resolve()))
    import importlib

    parallel_api = importlib.import_module("bearing_localization.search.parallel_screened")
    ParallelScreenedCandidatePool = getattr(parallel_api, "ParallelScreenedCandidatePool")
    ParallelScreenedError = getattr(parallel_api, "ParallelScreenedError")

    from bearing_localization.search.config import SearchConfig
    from bearing_localization.search.proposals.cache import (
        ProposalCacheContext,
        load_proposal_cache,
    )
    from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
    from maritime_localization_shared.geospatial.vector_map import VectorMap
    from maritime_localization_shared.serialization import loads_contract

    load_started = time.perf_counter()
    measurements = loads_contract(static_paths["measurements"].read_text(), MeasurementSetV2)
    catalog = loads_contract(static_paths["catalog"].read_text(), CatalogV2)
    map_input = loads_contract(static_paths["map"].read_text(), MapV2)
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    telemetry = json.loads((generation_run_root / "estimate.telemetry.json").read_text())
    cache_diag = telemetry["diagnostics"]["proposal_cache"]
    context = ProposalCacheContext(**cache_diag["context"])
    cache_result = load_proposal_cache(cache_path, context=context)
    stages["contract_and_cache_load_s"] = time.perf_counter() - load_started
    records, stages["coordinate_transform_and_cli_order_s"] = _candidate_records(
        cache_result, vector_map
    )
    if len(records) != len(cache_result.proposals):
        raise ValueError("Candidate ordering omitted proposal records.")
    ids = [row["candidate_id"] for row in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Candidate IDs are not unique.")
    if set(ids) != {f"cell:{proposal.candidate_id}" for proposal in cache_result.proposals}:
        raise ValueError("Candidate plan does not include every cached proposal ID exactly once.")
    append_jsonl(output_root / "candidate-plan.jsonl", records)
    original_config = json.loads(
        (
            search_root
            / "data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/search-config.json"
        ).read_text()
    )
    original_config.pop("proposal_cache_path", None)
    original_config["bounds_xy"] = tuple(original_config["bounds_xy"])
    config = SearchConfig(**original_config)
    chunk_path = output_root / "chunk-results.jsonl"
    hypotheses_path = output_root / "hypotheses.jsonl"
    totals = {
        "expected_candidate_count": len(records),
        "completed_candidate_count": 0,
        "exact_scored_count": 0,
        "invalid_water_count": 0,
        "unresolved_count": 0,
        "failed_candidate_count": 0,
        "pruned_candidate_count": 0,
        "completed_chunk_count": 0,
    }
    pool_error: dict[str, Any] | None = None
    pool_start = time.perf_counter()
    try:
        with ParallelScreenedCandidatePool(
            catalog,
            vector_map,
            workers=workers,
            memory_budget_bytes=memory_budget_bytes,
            chunk_size=chunk_size,
            request_timeout_s=timeout_s,
        ) as pool:
            stages["worker_pool_startup_s"] = time.perf_counter() - pool_start
            screened_api = importlib.import_module("bearing_localization.search.screened_search")
            CandidatePosition = getattr(screened_api, "CandidatePosition")
            candidates = (
                CandidatePosition(row["candidate_id"], tuple(row["position_xy_m"]))
                for row in records
            )
            stage_start = time.perf_counter()
            try:
                for chunk in pool.iter_chunks(
                    measurements,
                    config,
                    candidates,
                    mode="exact",
                    complete_candidate_count=len(records),
                ):
                    chunk_rows = [asdict(row) for row in chunk.candidates]
                    hyp_rows = [
                        {"input_index": index, "hypothesis": asdict(hypothesis)}
                        for index, hypothesis in chunk.hypotheses
                    ]
                    append_jsonl(
                        chunk_path,
                        [
                            {
                                "chunk_index": chunk.chunk_index,
                                "candidate_count": chunk.candidate_count,
                                "exact_scored_count": chunk.exact_scored_count,
                                "exact_evaluation_count": chunk.exact_evaluation_count,
                                "invalid_water_count": chunk.invalid_water_count,
                                "unresolved_count": chunk.unresolved_count,
                                "pruned_count": chunk.pruned_count,
                                "request_complete": chunk.request_complete,
                                "finite_candidate_set": chunk.finite_candidate_set,
                                "screened_elapsed_s": chunk.screened_elapsed_s,
                                "exact_elapsed_s": chunk.exact_elapsed_s,
                                "worker_evaluation_s": chunk.worker_evaluation_s,
                                "estimated_memory_bytes": chunk.estimated_memory_bytes,
                                "worker_rss_bytes": chunk.worker_rss_bytes,
                                "candidates": chunk_rows,
                            }
                        ],
                    )
                    append_jsonl(hypotheses_path, hyp_rows)
                    totals["completed_candidate_count"] += chunk.candidate_count
                    totals["exact_scored_count"] += chunk.exact_scored_count
                    totals["invalid_water_count"] += chunk.invalid_water_count
                    totals["unresolved_count"] += chunk.unresolved_count
                    totals["pruned_candidate_count"] += chunk.pruned_count
                    totals["completed_chunk_count"] += 1
                    write_json(
                        output_root / "checkpoint.json",
                        {"totals": totals, "pool_timings": asdict(pool.last_timings)},
                    )
            except ParallelScreenedError as exc:
                pool_error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "completed_count": exc.completed_count,
                    "failed_count": exc.failed_count,
                    "outstanding_count": exc.outstanding_count,
                    "failed_candidate_ids": list(exc.failed_candidate_ids),
                }
                totals["failed_candidate_count"] = exc.failed_count
            stages["ordered_exact_scoring_s"] = time.perf_counter() - stage_start
            pool_timings = asdict(pool.last_timings)
    except BaseException as exc:
        pool_error = pool_error or {"type": type(exc).__name__, "message": str(exc)}
        pool_timings = None
    planned = len(records)
    accounted = totals["completed_candidate_count"] + totals["failed_candidate_count"]
    totals["unvisited_candidate_count"] = max(0, planned - accounted)
    totals["request_complete"] = (
        pool_error is None
        and totals["completed_candidate_count"] == planned
        and totals["unresolved_count"] == 0
    )
    manifest = {
        "schema_version": 1,
        "run_id": "a26-cached-finite-domain-exact-v1",
        "purpose": "development finite cached-candidate diagnostic",
        "execution_mode": "exact",
        "proposal_generation_run_id": generation_manifest["run_id"],
        "search_source_sha": EXPECTED_SEARCH_SHA,
        "proposal_cache_tree_sha256": sha256_tree(cache_path),
        "proposal_cache_context_sha256": context.context_hash,
        "static_input_sha256": {name: sha256_file(path) for name, path in static_paths.items()},
        "candidate_plan_sha256": sha256_file(output_root / "candidate-plan.jsonl"),
        "candidate_order": "cli_geodesic_matching_votes_water_residual_evidence_cell_v1",
        "worker_config": {
            "workers": workers,
            "memory_budget_bytes": memory_budget_bytes,
            "chunk_size": chunk_size,
            "timeout_s": timeout_s,
        },
        "stages": stages,
        "pool_timings": pool_timings,
        "totals": totals,
        "pool_error": pool_error,
        "full_request_elapsed_s": time.monotonic() - wall_start,
    }
    write_json(output_root / "run-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", type=Path, required=True)
    parser.add_argument("--static-root", type=Path, required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--generation-run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--memory-budget-bytes", type=int, default=DEFAULT_MEMORY_BUDGET_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args()
    import fcntl

    project_root = args.search_root.resolve().parents[2]
    lock_path = project_root / ".agent-work/benchmark.lock"
    with lock_path.open("a+") as lock_stream:
        try:
            fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Benchmark lock is busy; do not queue a timed run.") from exc
        try:
            manifest = run_finite_domain(
                search_root=args.search_root,
                static_root=args.static_root,
                cache_path=args.cache_path,
                generation_run_root=args.generation_run_root,
                output_root=args.output_root,
                workers=args.workers,
                memory_budget_bytes=args.memory_budget_bytes,
                chunk_size=args.chunk_size,
                timeout_s=args.timeout_s,
            )
        finally:
            fcntl.flock(lock_stream, fcntl.LOCK_UN)
    print(json.dumps({"totals": manifest["totals"], "run_id": manifest["run_id"]}, sort_keys=True))
    return 0 if manifest["totals"]["request_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
