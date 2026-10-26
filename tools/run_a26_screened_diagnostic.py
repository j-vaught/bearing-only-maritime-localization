"""Prepare and optionally run the frozen A26 whole-proposal cache diagnostic.

The default mode only freezes inputs/configuration and prints the exact command.
Search requires an explicit gate digest and an exact source commit pin.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CATALOG_SHA256 = "2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704"
MAP_SHA256 = "cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93"
MEASUREMENTS_SHA256 = "ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1"
SCOPE_SHA256 = "d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1"
CATALOG_ARCHIVE_SHA256 = "cef2b3dec757983c8a026caeff98e1ba42c164660f32a34915594e1443ac5472"
MAP_ARCHIVE_SHA256 = "fda522364582a20d74a643e7e854844b2ce2490a45e469cb210fd9a8babd13de"
RUN_ID = "a26-screened-cachegen-port001-placement001-r0-10km-typesoff-v1"
TIMEOUT_S = 1800.0
EXECUTION_PROTOCOL = {
    "schema": "accuracy-execution-protocol-v1",
    "timeout_s": TIMEOUT_S,
    "timeout_status": "search_timeout",
    "clock": "monotonic_wall",
    "termination": {
        "scope": "process_group",
        "initial_signal": "SIGTERM",
        "grace_s": 1.0,
        "final_signal": "SIGKILL",
        "reap": True,
    },
}
CACHE_FORMAT = "geodesic-proposal-cache-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        content_hash = bytes.fromhex(sha256_file(item))
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(content_hash)
    return digest.hexdigest()


def _copy_verified(source: Path, target: Path, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_file(target) != expected_sha256:
            raise ValueError(f"Existing frozen input differs: {target}")
        return
    temporary = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Source hash mismatch while copying {source}")
    temporary.replace(target)


def _decompress_verified(source: Path, target: Path, archive_sha256: str, raw_sha256: str) -> None:
    if sha256_file(source) != archive_sha256:
        raise ValueError(f"Compressed input hash mismatch: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_file(target) != raw_sha256:
            raise ValueError(f"Existing frozen input differs: {target}")
        return
    temporary = target.with_suffix(target.suffix + ".tmp")
    with gzip.open(source, "rb") as input_stream, temporary.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
    if sha256_file(temporary) != raw_sha256:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Decompressed input hash mismatch: {source}")
    temporary.replace(target)


def _write_json_idempotent(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists() and path.read_bytes() != payload:
        raise FileExistsError(f"Refusing to overwrite frozen plan file {path}")
    if not path.exists():
        path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _git_sha(worktree: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=worktree, text=True).strip()


def _frozen_inputs(repo_root: Path, static_root: Path) -> dict[str, Path]:
    package_root = repo_root / "data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic"
    inputs = {
        "measurements": (
            package_root / "measurements.json",
            static_root / "measurements.json",
            MEASUREMENTS_SHA256,
        ),
        "scope_manifest": (
            package_root / "scope-manifest.json",
            static_root / "scope-manifest.json",
            SCOPE_SHA256,
        ),
    }
    for _, (source, target, expected) in inputs.items():
        _copy_verified(source, target, expected)
    _decompress_verified(
        repo_root / "data/catalogs/accuracy-inputs-v2/east-coast-fullcoast-catalog-v2.json.gz",
        static_root / "catalog.json",
        CATALOG_ARCHIVE_SHA256,
        CATALOG_SHA256,
    )
    _decompress_verified(
        repo_root
        / "data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz",
        static_root / "map.json",
        MAP_ARCHIVE_SHA256,
        MAP_SHA256,
    )
    return {name: target for name, (_, target, _) in inputs.items()} | {
        "catalog": static_root / "catalog.json",
        "map": static_root / "map.json",
    }


def _make_generation_config(repo_root: Path, run_root: Path, cache_path: Path) -> tuple[Path, str]:
    source = (
        repo_root / "data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/search-config.json"
    )
    config = json.loads(source.read_text(encoding="utf-8"))
    expected = {
        "algorithm": "wedge_geodesic",
        "max_range_m": 10000.0,
        "use_types": False,
        "objective_version": "bearing_set_map_v1",
        "proposal_source_pair_budget": 333340,
        "proposal_cell_budget": 5_000_000,
        "proposal_chunk_size": 4096,
        "proposal_solver_tolerance_deg": 1e-7,
        "generated_sigma_deg": 0.5,
        "bounds_xy": [
            -220366.49772326474,
            1424309.6784374337,
            2699585.3102704627,
            5088044.413143414,
        ],
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f"Frozen SearchConfig mismatch for {key}.")
    if config.get("candidate_budget") != 256:
        raise ValueError("The source SearchConfig candidate budget changed.")
    config["candidate_budget"] = 1
    config["proposal_cache_path"] = str(cache_path.resolve())
    config_path = run_root / "search-config.json"
    return config_path, _write_json_idempotent(config_path, config)


def build_cache_generation_plan(
    repo_root: Path, search_worktree: Path, output_root: Path
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    search_worktree = search_worktree.resolve()
    output_root = output_root.resolve()
    static_root = output_root / "static-inputs"
    cache_path = output_root / "cache/proposals-v1"
    run_root = output_root / "cache-build"
    if cache_path.exists() or cache_path.is_symlink():
        raise FileExistsError(f"Fresh cache destination required: {cache_path}")
    static_inputs = _frozen_inputs(repo_root, static_root)
    config_path, config_sha256 = _make_generation_config(repo_root, run_root, cache_path)
    input_hashes = {name: sha256_file(path) for name, path in static_inputs.items()}
    expected_hashes = {
        "measurements": MEASUREMENTS_SHA256,
        "scope_manifest": SCOPE_SHA256,
        "catalog": CATALOG_SHA256,
        "map": MAP_SHA256,
    }
    if input_hashes != expected_hashes:
        raise ValueError(f"Frozen input hash mismatch: {input_hashes}")
    argv = [
        "uv",
        "run",
        "bearing-localization-search",
        "--schema-version",
        "2",
        "--algorithm",
        "wedge_geodesic",
        "--wedge-cell-size-m",
        "250",
        "--wedge-tolerance-deg",
        "0.5",
        "--no-refine",
        "--measurements",
        str(static_inputs["measurements"]),
        "--catalog",
        str(static_inputs["catalog"]),
        "--map",
        str(static_inputs["map"]),
        "--config",
        str(config_path),
        "--projected-crs",
        "EPSG:32618",
        "--scope-manifest",
        str(static_inputs["scope_manifest"]),
        "--output",
        str(run_root / "estimate.json"),
        "--telemetry-output",
        str(run_root / "estimate.telemetry.json"),
    ]
    if any("truth" in item.lower() or "evaluator" in item.lower() for item in argv):
        raise ValueError("Search argv must not contain truth or evaluator paths.")
    return {
        "schema_version": 1,
        "run_id": RUN_ID,
        "purpose": "development cache-generation diagnostic",
        "search_source_sha": _git_sha(search_worktree),
        "runner_source_sha": _git_sha(repo_root),
        "timeout_s": TIMEOUT_S,
        "search_truth_input": None,
        "cache_format": CACHE_FORMAT,
        "cache_path": str(cache_path),
        "input_sha256": input_hashes,
        "config_sha256": config_sha256,
        "budgets": {
            "source_pairs": 333340,
            "candidate_cells": 5_000_000,
            "chunk_size": 4096,
            "exact_scoring_candidates": 1,
            "refinement": "disabled",
        },
        "argv": argv,
        "stage": "cache_generation_only",
    }


def benchmark_lock_path(search_worktree: Path) -> Path:
    project_root = search_worktree.resolve().parents[2]
    return project_root / Path(".agent-work/benchmark.lock")


def execute_cache_generation(
    plan: dict[str, Any], search_worktree: Path, output_root: Path, *, gate_sha: str
) -> dict[str, Any]:
    import re

    if not re.fullmatch(r"[0-9a-f]{40,64}", gate_sha):
        raise ValueError("A gate commit digest is required before execution.")
    if _git_sha(search_worktree.resolve()) != plan["search_source_sha"]:
        raise ValueError("Search source changed after the plan was frozen.")
    output_root = output_root.resolve()
    lock_path = search_worktree.resolve() / ".agent-work/benchmark.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    run_root = output_root / "cache-build"
    if (run_root / "run-manifest.json").exists():
        raise FileExistsError("Cache-generation run manifest already exists.")
    if Path(plan["cache_path"]).exists():
        raise FileExistsError("Cache path already exists; a new run identity is required.")
    lock_stream = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Benchmark lock is busy; do not queue a timed run.") from exc
        started_utc = datetime.now(UTC).isoformat()
        started = time.monotonic()
        sys.path.insert(0, str(search_worktree.resolve()))
        from tools.run_accuracy_suite import _run_process

        result = _run_process(plan["argv"], cwd=search_worktree.resolve(), timeout_s=TIMEOUT_S)
        wall_s = time.monotonic() - started
        estimate_path = run_root / "estimate.json"
        telemetry_path = run_root / "estimate.telemetry.json"
        telemetry = json.loads(telemetry_path.read_text()) if telemetry_path.is_file() else None
        cache_path = Path(plan["cache_path"])
        manifest = {
            **plan,
            "gate_sha": gate_sha,
            "started_utc": started_utc,
            "finished_utc": datetime.now(UTC).isoformat(),
            "external_wall_s": wall_s,
            "process_result": {
                k: result.get(k)
                for k in ("status", "returncode", "elapsed_s", "process_group_terminated")
            },
            "stdout_path": str(run_root / "stdout.txt"),
            "stderr_path": str(run_root / "stderr.txt"),
            "estimate_sha256": sha256_file(estimate_path) if estimate_path.is_file() else None,
            "telemetry_sha256": sha256_file(telemetry_path) if telemetry_path.is_file() else None,
            "telemetry_stages": telemetry.get("stages") if telemetry else None,
            "proposal_cache": telemetry.get("diagnostics", {}).get("proposal_cache")
            if telemetry
            else None,
            "cache_tree_sha256": tree_sha256(cache_path) if cache_path.is_dir() else None,
            "cache_bytes": sum(
                path.stat().st_size for path in cache_path.rglob("*") if path.is_file()
            )
            if cache_path.is_dir()
            else None,
        }
        (run_root / "stdout.txt").write_text(result.get("stdout", ""), encoding="utf-8")
        (run_root / "stderr.txt").write_text(result.get("stderr", ""), encoding="utf-8")
        _write_json_idempotent(run_root / "run-manifest.json", manifest)
        return manifest
    finally:
        fcntl.flock(lock_stream, fcntl.LOCK_UN)
        lock_stream.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--search-worktree", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--gate-sha", help="Required only with --execute-cache-build.")
    parser.add_argument("--execute-cache-build", action="store_true")
    args = parser.parse_args()
    plan = build_cache_generation_plan(args.repo_root, args.search_worktree, args.output_root)
    run_root = args.output_root.resolve() / "cache-build"
    if args.execute_cache_build:
        if args.gate_sha is None:
            parser.error("--gate-sha is required with --execute-cache-build.")
        result = execute_cache_generation(
            plan, args.search_worktree, args.output_root, gate_sha=args.gate_sha
        )
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in ("run_id", "process_result", "external_wall_s", "cache_tree_sha256")
                },
                sort_keys=True,
            )
        )
        return 0 if result["process_result"]["status"] == "completed" else 1
    plan_path = _write_json_idempotent(run_root / "command-plan.json", plan)
    print(
        json.dumps(
            {
                "status": "prepared_no_search",
                "plan_path": str(run_root / "command-plan.json"),
                "plan_sha256": plan_path,
                "argv": plan["argv"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
