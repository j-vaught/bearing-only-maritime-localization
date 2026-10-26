"""Score every cached geodesic proposal with ordered, durable chunk checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    _fsync_directory(path.parent)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    _fsync_directory(path.parent)


def _read_chunk_records(output_root: Path) -> list[dict[str, Any]]:
    chunk_dir = output_root / "chunk-results"
    if chunk_dir.is_dir():
        return [json.loads(path.read_text()) for path in sorted(chunk_dir.glob("chunk-*.json"))]
    return _read_jsonl(output_root / "chunk-results.jsonl")


def write_candidate_dispositions(
    output_root: Path,
    *,
    externally_interrupted: bool = False,
    failed_candidate_ids: list[str] | None = None,
    not_dispatched_candidate_ids: list[str] | None = None,
    failed_count_unattributed: int = 0,
    selected_candidate_ids: set[str] | None = None,
    candidate_prefix_limit: int | None = None,
) -> dict[str, int]:
    """Record one final disposition for each candidate in the immutable plan."""
    plan = _read_jsonl(output_root / "candidate-plan.jsonl")
    inventory = _read_jsonl(output_root / "candidate-inventory.jsonl")
    if not plan:
        plan = inventory
    elif inventory and {row["candidate_id"] for row in plan} != {
        row["candidate_id"] for row in inventory
    }:
        raise ValueError("Ordered candidate plan does not match the complete cache inventory.")
    candidate_ids = [row["candidate_id"] for row in plan]
    if candidate_prefix_limit is not None:
        if plan:
            selected_candidate_ids = set(candidate_ids[:candidate_prefix_limit])
        elif inventory:
            selected_candidate_ids = set()
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Candidate inventory or plan contains duplicate IDs.")
    chunk_records = _read_chunk_records(output_root)
    committed_rows = [
        (chunk, candidate)
        for chunk in chunk_records
        for candidate in chunk.get("candidates", [])
        if candidate.get("candidate_id") is not None
    ]
    committed_ids = [candidate["candidate_id"] for _, candidate in committed_rows]
    if len(committed_ids) != len(set(committed_ids)):
        raise ValueError("Durable chunk records contain duplicate candidate IDs.")
    committed: dict[str, dict[str, Any]] = {
        candidate["candidate_id"]: {
            "action": candidate.get("action"),
            "input_index": candidate.get("input_index"),
            "chunk_index": chunk.get("chunk_index"),
        }
        for chunk, candidate in committed_rows
    }
    failed_list = failed_candidate_ids or []
    not_dispatched_list = not_dispatched_candidate_ids or []
    if len(failed_list) != len(set(failed_list)):
        raise ValueError("Worker failure list contains duplicate candidate IDs.")
    if len(not_dispatched_list) != len(set(not_dispatched_list)):
        raise ValueError("Not-dispatched list contains duplicate candidate IDs.")
    known_ids = set(candidate_ids)
    if (set(committed_ids) | set(failed_list) | set(not_dispatched_list)) - known_ids:
        raise ValueError("Chunk or failure evidence refers to an ID outside the frozen inventory.")
    if failed_count_unattributed < 0 or failed_count_unattributed < len(failed_list):
        raise ValueError("Aggregate worker failure count is inconsistent with explicit failed IDs.")
    failed_ids = set(failed_list)
    not_dispatched_ids = set(not_dispatched_list)
    if failed_ids & not_dispatched_ids:
        raise ValueError("A candidate cannot be both failed and not dispatched.")
    counts = {
        "committed": 0,
        "failed": 0,
        "failed_unattributed": failed_count_unattributed - len(failed_list),
        "not_selected": 0,
        "dispatched_uncommitted_or_unknown": 0,
        "not_dispatched": 0,
        "unvisited_after_worker_exit": 0,
    }
    lines = []
    for index, row in enumerate(plan):
        candidate_id = row["candidate_id"]
        result = committed.get(candidate_id)
        if selected_candidate_ids is not None and candidate_id not in selected_candidate_ids:
            status = (
                "not_selected_by_explicit_prefix_profile"
                if plan
                else "prefix_ordering_not_reached_before_interruption"
            )
            counts["not_selected"] += 1
        elif result is not None:
            status = result["action"] or "durably_recorded"
            counts["committed"] += 1
        elif candidate_id in failed_ids:
            status = "worker_reported_failure"
            counts["failed"] += 1
        elif candidate_id in not_dispatched_ids:
            status = "not_dispatched"
            counts["not_dispatched"] += 1
        elif externally_interrupted:
            status = "worker_state_unknown_at_interruption"
            counts["dispatched_uncommitted_or_unknown"] += 1
        else:
            status = "unvisited_after_worker_exit"
            counts["unvisited_after_worker_exit"] += 1
        lines.append(
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "plan_index": index,
                    "status": status,
                    "evidence": result,
                },
                sort_keys=True,
                allow_nan=False,
            )
        )
    write_text_atomic(
        output_root / "candidate-dispositions.jsonl",
        "\n".join(lines) + ("\n" if lines else ""),
    )
    return counts


def write_jsonl_atomic(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    _fsync_directory(path.parent)


def ensure_inventory_from_verified_cache(
    output_root: Path, cache_path: Path, generation_run_root: Path
) -> int:
    """Recover the complete ID ledger directly from verified cache metadata."""
    manifest = json.loads((generation_run_root / "run-manifest.json").read_text())
    if manifest.get("process_result", {}).get("status") != "completed":
        raise ValueError("Cannot enumerate IDs from an incomplete cache-generation run.")
    if manifest.get("cache_path") != str(cache_path.resolve()):
        raise ValueError("Cache-generation manifest does not bind this cache path.")
    if manifest.get("cache_tree_sha256") != sha256_tree(cache_path):
        raise ValueError("Cache tree hash mismatch while recovering candidate IDs.")
    envelope = json.loads((cache_path / "metadata.json").read_text())
    core = envelope.get("metadata")
    canonical_metadata = json.dumps(
        core, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if (
        not isinstance(core, dict)
        or envelope.get("metadata_sha256") != hashlib.sha256(canonical_metadata).hexdigest()
    ):
        raise ValueError("Cache metadata hash is invalid while recovering candidate IDs.")
    proposal_ids = core.get("proposal_ids")
    if not isinstance(proposal_ids, list) or len(proposal_ids) != core.get("proposal_count"):
        raise ValueError("Cache metadata does not contain the complete proposal ID set.")
    candidate_ids = [f"cell:{proposal_id}" for proposal_id in proposal_ids]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Cache metadata contains duplicate proposal IDs.")
    inventory_path = output_root / "candidate-inventory.jsonl"
    if inventory_path.is_file():
        rows = _read_jsonl(inventory_path)
        existing_ids = [row["candidate_id"] for row in rows]
        if existing_ids != candidate_ids:
            raise ValueError("Persisted candidate inventory differs from verified cache IDs.")
    else:
        write_jsonl_atomic(
            inventory_path, [{"candidate_id": candidate_id} for candidate_id in candidate_ids]
        )
    return len(candidate_ids)


def append_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()


def verify_clean_source_root(root: Path, expected_sha: str, label: str) -> str:
    root = root.resolve()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if actual != expected_sha:
        raise ValueError(f"{label} source tree does not match its explicitly pinned SHA.")
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if status.strip():
        raise ValueError(f"{label} source tree is dirty; refusing to execute unpinned code.")
    return actual


def validate_generation_provenance(
    *,
    generation_root: Path,
    expected_generation_source_sha: str,
    run_root: Path,
    static_root: Path,
    cache_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Validate raw input, telemetry, implementation, and cache bindings."""
    generation_root = generation_root.resolve()
    verify_clean_source_root(generation_root, expected_generation_source_sha, "Generation")
    sys.path.insert(0, str(generation_root))
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
    if manifest.get("search_source_sha") != expected_generation_source_sha:
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
    implementation_sha = implementation_hash(generation_root)
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


def validate_runtime_config_binding(
    config: Any,
    generation_settings: dict[str, Any],
    measurements: Any,
    catalog: Any,
    vector_map: Any,
) -> None:
    """Require scorer settings to match every cache-bound config dimension."""
    effective_sigmas = []
    for index, observation in enumerate(measurements.measurements):
        declared = observation.sigma_deg
        sigma = config.generated_sigma_deg if declared is None else declared
        effective_sigmas.append([index, max(sigma, config.sigma_floor_deg)])
    configured_source_budget = config.proposal_source_pair_budget
    effective_source_budget = configured_source_budget
    if effective_source_budget is None:
        effective_source_budget = max(1, len(measurements.measurements) * len(catalog.records))
    expected = {
        "algorithm": config.algorithm,
        "max_range_m": config.max_range_m,
        "min_range_m": config.min_range_m,
        "range_inclusive": config.range_inclusive,
        "use_types": config.use_types,
        "generated_sigma_deg": config.generated_sigma_deg,
        "sigma_floor_deg": config.sigma_floor_deg,
        "effective_observation_sigmas_deg": effective_sigmas,
        "bounds_xy": list(config.bounds_xy or ()),
        "map_projected_crs": vector_map.crs.to_string(),
        "proposal_source_pair_budget_configured": configured_source_budget,
        "max_source_pairs": effective_source_budget,
        "max_candidate_cells": config.proposal_cell_budget,
        "source_chunk_size": config.proposal_chunk_size,
        "solver_tolerance_deg": config.proposal_solver_tolerance_deg,
        "cell_size_m": 250.0,
        "angular_tolerance_deg": 0.5,
    }
    for key, expected_value in expected.items():
        if generation_settings.get(key) != expected_value:
            raise ValueError(f"Scoring config does not match cache-bound generation setting {key}.")


def smoke_scoring_source(scoring_root: Path, expected_scoring_source_sha: str) -> str:
    """Verify the pinned scoring checkout and import A30 in a clean subprocess."""
    scoring_root = scoring_root.resolve()
    actual_sha = verify_clean_source_root(scoring_root, expected_scoring_source_sha, "Scoring")
    module_path = scoring_root / "bearing_localization/search/parallel_screened.py"
    if not module_path.is_file():
        raise ValueError("Pinned scoring source does not contain the A30 worker pool.")
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(scoring_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from bearing_localization.search.parallel_screened import "
            "ParallelScreenedCandidatePool\n"
            "print(ParallelScreenedCandidatePool.__name__)",
        ],
        cwd=scoring_root,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    if result.stdout.strip() != "ParallelScreenedCandidatePool":
        raise ValueError("Scoring source subprocess import returned unexpected output.")
    return actual_sha


def run_finite_domain(
    *,
    generation_root: Path,
    scoring_root: Path,
    expected_generation_source_sha: str,
    expected_scoring_source_sha: str,
    static_root: Path,
    cache_path: Path,
    generation_run_root: Path,
    output_root: Path,
    workers: int = DEFAULT_WORKERS,
    memory_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    candidate_prefix_limit: int | None = None,
) -> dict[str, Any]:
    """Load a verified cache and exact-score its entire ordered candidate domain."""
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Fresh finite-domain output directory required: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if workers < 1 or chunk_size < 1 or memory_budget_bytes < 1 or timeout_s <= 0:
        raise ValueError("workers, chunk size, memory budget, and timeout must be positive.")
    if candidate_prefix_limit is not None and candidate_prefix_limit < 1:
        raise ValueError("candidate prefix limit must be positive when supplied.")
    wall_start = time.monotonic()
    stages: dict[str, float] = {}
    provenance_start = time.perf_counter()
    generation_manifest, static_paths = validate_generation_provenance(
        generation_root=generation_root,
        expected_generation_source_sha=expected_generation_source_sha,
        run_root=generation_run_root,
        static_root=static_root,
        cache_path=cache_path,
    )
    stages["generation_provenance_validation_s"] = time.perf_counter() - provenance_start
    scoring_root = scoring_root.resolve()
    actual_scoring_sha = smoke_scoring_source(scoring_root, expected_scoring_source_sha)
    for module_name in tuple(sys.modules):
        if module_name == "bearing_localization" or module_name.startswith("bearing_localization."):
            del sys.modules[module_name]
        elif module_name == "maritime_localization_shared" or module_name.startswith(
            "maritime_localization_shared."
        ):
            del sys.modules[module_name]
    sys.path[:] = [
        entry for entry in sys.path if Path(entry or ".").resolve() != generation_root.resolve()
    ]
    sys.path.insert(0, str(scoring_root))
    import importlib

    parallel_api = importlib.import_module("bearing_localization.search.parallel_screened")
    ParallelScreenedCandidatePool = getattr(parallel_api, "ParallelScreenedCandidatePool")
    ParallelScreenedError = getattr(parallel_api, "ParallelScreenedError")

    from bearing_localization.search.config_serialization import loads_search_config
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
    inventory_path = output_root / "candidate-inventory.jsonl"
    inventory_tmp = inventory_path.with_suffix(".jsonl.tmp")
    with inventory_tmp.open("w", encoding="utf-8") as inventory_stream:
        for proposal in cache_result.proposals:
            inventory_stream.write(
                json.dumps({"candidate_id": f"cell:{proposal.candidate_id}"}, sort_keys=True) + "\n"
            )
        inventory_stream.flush()
        os.fsync(inventory_stream.fileno())
    inventory_tmp.replace(inventory_path)
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
    write_jsonl_atomic(output_root / "candidate-plan.jsonl", records)
    selected_records = records[:candidate_prefix_limit] if candidate_prefix_limit else records
    selected_ids = {row["candidate_id"] for row in selected_records}
    original_config = json.loads(
        (
            scoring_root
            / "data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/search-config.json"
        ).read_text()
    )
    original_config.pop("proposal_cache_path", None)
    config = loads_search_config(json.dumps(original_config))
    validate_runtime_config_binding(
        config,
        cache_diag["generation_config"],
        measurements,
        catalog,
        vector_map,
    )
    chunk_dir = output_root / "chunk-results"
    hypotheses_dir = output_root / "hypotheses"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    hypotheses_dir.mkdir(parents=True, exist_ok=True)
    totals = {
        "candidate_domain_count": len(records),
        "expected_candidate_count": len(selected_records),
        "not_selected_candidate_count": len(records) - len(selected_records),
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
                for row in selected_records
            )
            stage_start = time.perf_counter()
            try:
                for chunk in pool.iter_chunks(
                    measurements,
                    config,
                    candidates,
                    mode="exact",
                    complete_candidate_count=len(selected_records),
                ):
                    chunk_rows = [asdict(row) for row in chunk.candidates]
                    hyp_rows = [
                        {"input_index": index, "hypothesis": asdict(hypothesis)}
                        for index, hypothesis in chunk.hypotheses
                    ]
                    write_json(
                        chunk_dir / f"chunk-{chunk.chunk_index:08d}.json",
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
                        },
                    )
                    write_json(hypotheses_dir / f"chunk-{chunk.chunk_index:08d}.json", hyp_rows)
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
                    "disposition": asdict(exc.disposition),
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
    selected_count = len(selected_records)
    accounted = totals["completed_candidate_count"] + totals["failed_candidate_count"]
    totals["unvisited_candidate_count"] = max(0, selected_count - accounted)
    totals["selected_request_complete"] = (
        pool_error is None and totals["completed_candidate_count"] == selected_count
    )
    full_domain_selected = selected_count == len(records)
    totals["all_candidates_accounted"] = (
        full_domain_selected and totals["selected_request_complete"]
    )
    totals["request_complete"] = totals["selected_request_complete"]
    totals["finite_candidate_set_certified"] = (
        totals["all_candidates_accounted"] and totals["unresolved_count"] == 0
    )
    disposition_counts = write_candidate_dispositions(
        output_root,
        externally_interrupted=pool_error is not None,
        failed_candidate_ids=(pool_error or {}).get("failed_candidate_ids", []),
        not_dispatched_candidate_ids=(pool_error or {})
        .get("disposition", {})
        .get("not_dispatched_candidate_ids", []),
        failed_count_unattributed=(pool_error or {}).get("failed_count", 0),
        selected_candidate_ids=selected_ids,
        candidate_prefix_limit=candidate_prefix_limit,
    )
    manifest = {
        "schema_version": 1,
        "run_id": (
            "a26-cached-finite-domain-prefix-v1"
            if candidate_prefix_limit is not None
            else "a26-cached-finite-domain-exact-v1"
        ),
        "purpose": (
            "development bounded prefix runtime profile; no accuracy claim"
            if candidate_prefix_limit is not None
            else "development finite cached-candidate diagnostic"
        ),
        "execution_scope": (
            "bounded_ordered_prefix_profile"
            if candidate_prefix_limit is not None
            else "complete_finite_candidate_domain"
        ),
        "candidate_prefix_limit": candidate_prefix_limit,
        "execution_mode": "exact",
        "proposal_generation_run_id": generation_manifest["run_id"],
        "generation_source_sha": expected_generation_source_sha,
        "scoring_source_sha": actual_scoring_sha,
        "scoring_module_sha256": sha256_file(
            scoring_root / "bearing_localization/search/parallel_screened.py"
        ),
        "scoring_dependency_lock_sha256": sha256_file(scoring_root / "uv.lock"),
        "scoring_config_sha256": sha256_file(
            scoring_root
            / "data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/search-config.json"
        ),
        "proposal_cache_tree_sha256": sha256_tree(cache_path),
        "proposal_cache_context_sha256": context.context_hash,
        "static_input_sha256": {name: sha256_file(path) for name, path in static_paths.items()},
        "candidate_plan_sha256": sha256_file(output_root / "candidate-plan.jsonl"),
        "chunk_results_sha256": sha256_tree(output_root / "chunk-results"),
        "hypotheses_sha256": sha256_tree(output_root / "hypotheses"),
        "candidate_order": "cli_geodesic_matching_votes_water_residual_evidence_cell_v1",
        "worker_config": {
            "workers": workers,
            "memory_budget_bytes": memory_budget_bytes,
            "chunk_size": chunk_size,
            "timeout_s": timeout_s,
            "timeout_scope": "pool_request_only; outer supervisor covers complete request",
        },
        "stages": stages,
        "pool_timings": pool_timings,
        "totals": totals,
        "pool_error": pool_error,
        "candidate_disposition_counts": disposition_counts,
        "full_request_elapsed_s": time.monotonic() - wall_start,
    }
    write_json(output_root / "run-manifest.json", manifest)
    return manifest


def resolve_project_root(scoring_root: Path) -> Path:
    """Resolve the shared repository root from Git's common directory."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(scoring_root.resolve()),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    common_git_dir = Path(result.stdout.strip()).resolve()
    project_root = common_git_dir.parent
    if not (project_root / ".agent-work").is_dir():
        raise ValueError(f"Shared repository root {project_root} has no .agent-work directory.")
    return project_root


def worker_exit_code(manifest: dict[str, Any]) -> int:
    """Return success for a complete selected request, including explicit profiles."""
    return 0 if manifest["totals"]["selected_request_complete"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-root", type=Path, required=True)
    parser.add_argument("--scoring-root", type=Path, required=True)
    parser.add_argument("--expected-generation-source-sha", required=True)
    parser.add_argument("--expected-scoring-source-sha", required=True)
    parser.add_argument("--static-root", type=Path, required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--generation-run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--memory-budget-bytes", type=int, default=DEFAULT_MEMORY_BUDGET_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--candidate-prefix-limit", type=int)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        manifest = run_finite_domain(
            generation_root=args.generation_root,
            scoring_root=args.scoring_root,
            expected_generation_source_sha=args.expected_generation_source_sha,
            expected_scoring_source_sha=args.expected_scoring_source_sha,
            static_root=args.static_root,
            cache_path=args.cache_path,
            generation_run_root=args.generation_run_root,
            output_root=args.output_root,
            workers=args.workers,
            memory_budget_bytes=args.memory_budget_bytes,
            chunk_size=args.chunk_size,
            timeout_s=args.timeout_s,
            candidate_prefix_limit=args.candidate_prefix_limit,
        )
    else:
        import fcntl

        project_root = resolve_project_root(args.scoring_root)
        lock_path = project_root / ".agent-work/benchmark.lock"
        with lock_path.open("a+") as lock_stream:
            try:
                fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Benchmark lock is busy; do not queue a timed run.") from exc
            try:
                manifest = supervise_complete_request(args)
            finally:
                fcntl.flock(lock_stream, fcntl.LOCK_UN)
    print(json.dumps({"totals": manifest["totals"], "run_id": manifest["run_id"]}, sort_keys=True))
    return worker_exit_code(manifest)


def terminate_process_group(process: subprocess.Popen[Any], *, grace_s: float = 1.0) -> None:
    """Terminate the whole worker process group and reap its leader."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    try:
        # Kill any descendants still alive even if the group leader exited on TERM.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise_complete_request(args: argparse.Namespace) -> dict[str, Any]:
    """Enforce one process-group deadline over loading, planning, scoring, and output."""
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Fresh finite-domain output directory required: {output_root}")
    log_path = output_root.parent / f"{output_root.name}.supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--_worker"]
    command = [part for part in command if part != "--_worker"] + ["--_worker"]
    started = time.monotonic()
    with log_path.open("wb") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=args.scoring_root.resolve(),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=args.timeout_s)
        except subprocess.TimeoutExpired:
            terminate_process_group(process, grace_s=1.0)
            output_root.mkdir(parents=True, exist_ok=True)
            enumerated_candidate_count = ensure_inventory_from_verified_cache(
                output_root, args.cache_path.resolve(), args.generation_run_root.resolve()
            )
            dispositions = write_candidate_dispositions(
                output_root,
                externally_interrupted=True,
                candidate_prefix_limit=args.candidate_prefix_limit,
            )
            timeout_manifest = {
                "schema_version": 1,
                "run_id": "a26-cached-finite-domain-exact-v1",
                "status": "complete_request_timeout",
                "timeout_s": args.timeout_s,
                "pool_request_timeout_s": args.timeout_s,
                "timeout_scope": (
                    "outer deadline covers the full request; pool deadline covers only pool work"
                ),
                "full_request_elapsed_s": time.monotonic() - started,
                "termination": "process_group_sigterm_then_sigkill_reaped",
                "log_path": str(log_path),
                "generation_source_sha": args.expected_generation_source_sha,
                "scoring_source_sha": args.expected_scoring_source_sha,
                "candidate_disposition_counts": dispositions,
                "enumerated_candidate_count": enumerated_candidate_count,
                "candidate_prefix_limit": args.candidate_prefix_limit,
            }
            write_json(output_root / "supervisor-manifest.json", timeout_manifest)
            raise TimeoutError(f"Complete request exceeded {args.timeout_s}s; see {log_path}.")
    if return_code != 0:
        output_root.mkdir(parents=True, exist_ok=True)
        enumerated_candidate_count = ensure_inventory_from_verified_cache(
            output_root, args.cache_path.resolve(), args.generation_run_root.resolve()
        )
        worker_manifest_path = output_root / "run-manifest.json"
        worker_manifest = (
            json.loads(worker_manifest_path.read_text()) if worker_manifest_path.is_file() else {}
        )
        worker_failures = (worker_manifest.get("pool_error") or {}).get("failed_candidate_ids", [])
        dispositions = write_candidate_dispositions(
            output_root,
            externally_interrupted=True,
            failed_candidate_ids=worker_failures,
            not_dispatched_candidate_ids=(worker_manifest.get("pool_error") or {})
            .get("disposition", {})
            .get("not_dispatched_candidate_ids", []),
            candidate_prefix_limit=args.candidate_prefix_limit,
            failed_count_unattributed=(worker_manifest.get("pool_error") or {}).get(
                "failed_count", len(worker_failures)
            ),
        )
        write_json(
            output_root / "supervisor-manifest.json",
            {
                "schema_version": 1,
                "run_id": "a26-cached-finite-domain-exact-v1",
                "status": "worker_failed",
                "worker_returncode": return_code,
                "timeout_s": args.timeout_s,
                "pool_request_timeout_s": args.timeout_s,
                "timeout_scope": (
                    "outer deadline covers the full request; pool deadline covers only pool work"
                ),
                "full_request_elapsed_s": time.monotonic() - started,
                "log_path": str(log_path),
                "generation_source_sha": args.expected_generation_source_sha,
                "scoring_source_sha": args.expected_scoring_source_sha,
                "candidate_disposition_counts": dispositions,
                "enumerated_candidate_count": enumerated_candidate_count,
                "candidate_prefix_limit": args.candidate_prefix_limit,
                "pool_error": worker_manifest.get("pool_error"),
            },
        )
        raise RuntimeError(f"Finite-domain worker exited {return_code}; see {log_path}.")
    manifest_path = output_root / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["external_supervisor"] = {
        "timeout_s": args.timeout_s,
        "full_request_elapsed_s": time.monotonic() - started,
        "status": "completed",
        "log_path": str(log_path),
        "termination": "process_group_sigterm_then_sigkill_reaped",
    }
    write_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    raise SystemExit(main())
