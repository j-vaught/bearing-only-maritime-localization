"""Bounded score-only refinement of committed cached coarse candidates.

This diagnostic reads no evaluator truth. Candidate counts and resource stops
remain explicit; local refinement does not establish full-domain completeness.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.refinement import refine_modes
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import Hypothesis
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import contract_from_dict, read_contract

FLOOR = 40 * 1024**3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        name = item.relative_to(path).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(bytes.fromhex(sha256(item)))
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    def reject(value: str) -> None:
        raise ValueError(f"Nonfinite JSON number {value}")

    return json.loads(path.read_text(), parse_constant=reject)


@dataclass(frozen=True)
class Seed:
    input_index: int
    candidate_id: str
    hypothesis: Hypothesis
    chunk_name: str
    chunk_sha256: str
    sidecar_sha256: str

    @property
    def cell(self) -> tuple[int, int]:
        parts = self.candidate_id.split(":")
        return int(parts[-2]), int(parts[-1])

    @property
    def order(self) -> tuple[float, float, float, str]:
        h = self.hypothesis
        return h.score, h.east_m, h.north_m, self.candidate_id


def load_committed(root: Path) -> tuple[list[Seed], dict[str, Any]]:
    identity = read_json(root / "run-identity.json")
    core = {k: v for k, v in identity.items() if k != "identity_sha256"}
    if canonical_hash(core) != identity["identity_sha256"]:
        raise ValueError("Run identity hash mismatch")
    supervisor = read_json(root / "supervisor-manifest.json")
    if supervisor["run_identity_sha256"] != identity["identity_sha256"]:
        raise ValueError("Supervisor identity mismatch")
    for name, key in (
        ("candidate-plan.jsonl", "candidate_plan_sha256"),
        ("candidate-inventory.jsonl", "candidate_inventory_sha256"),
        ("candidate-dispositions.jsonl", "candidate_dispositions_sha256"),
    ):
        if sha256(root / name) != supervisor[key]:
            raise ValueError(f"Frozen file hash mismatch: {name}")
    for name, key in (
        ("chunk-results", "chunk_results_sha256"),
        ("hypotheses", "hypotheses_sha256"),
    ):
        if tree_hash(root / name) != supervisor[key]:
            raise ValueError(f"Frozen tree hash mismatch: {name}")
    seeds: list[Seed] = []
    indices: set[int] = set()
    ids: set[str] = set()
    chunks = sorted((root / "chunk-results").glob("chunk-*.json"))
    for ordinal, path in enumerate(chunks):
        chunk = read_json(path)
        if chunk["chunk_index"] != ordinal or path.name != f"chunk-{ordinal:08d}.json":
            raise ValueError("Noncontiguous chunk sequence")
        sidecar_path = root / "hypotheses" / path.name
        digest = sha256(sidecar_path)
        if digest != chunk["hypotheses_sha256"]:
            raise ValueError("Sidecar hash mismatch")
        candidates = {row["input_index"]: row for row in chunk["candidates"]}
        if len(candidates) != len(chunk["candidates"]):
            raise ValueError("Duplicate chunk input index")
        sidecars = read_json(sidecar_path)
        if {row["input_index"] for row in sidecars} != {
            i for i, row in candidates.items() if row["action"] == "exact_scored"
        }:
            raise ValueError("Scored and saved hypothesis populations differ")
        for row in sidecars:
            index = row["input_index"]
            candidate = candidates[index]
            identifier = candidate["candidate_id"]
            hypothesis = contract_from_dict(Hypothesis, row["hypothesis"])
            if index in indices or identifier in ids:
                raise ValueError("Duplicate committed hypothesis")
            if hypothesis.score != candidate["score"] or (
                hypothesis.east_m,
                hypothesis.north_m,
            ) != tuple(candidate["position_xy_m"]):
                raise ValueError("Hypothesis differs from durable candidate")
            indices.add(index)
            ids.add(identifier)
            seeds.append(Seed(index, identifier, hypothesis, path.name, sha256(path), digest))
    orphans = sorted(
        p.name
        for p in (root / "hypotheses").glob("chunk-*.json")
        if not (root / "chunk-results" / p.name).exists()
    )
    if orphans and orphans != [f"chunk-{len(chunks):08d}.json"]:
        raise ValueError("Unexpected orphan sidecar")
    return seeds, {
        "identity": identity,
        "supervisor_sha256": sha256(root / "supervisor-manifest.json"),
        "committed_hypothesis_count": len(seeds),
        "orphan_sidecars_excluded": orphans,
    }


def select_modes(seeds: list[Seed], cap: int = 64, separation_m: float = 100.0) -> list[Seed]:
    if type(cap) is not int or cap <= 0 or not math.isfinite(separation_m) or separation_m < 0:
        raise ValueError("Invalid mode selection settings")
    by_cell = {seed.cell: seed for seed in seeds}
    if len(by_cell) != len(seeds):
        raise ValueError("Repeated grid cell")
    selected: list[Seed] = []
    for seed in sorted(seeds, key=lambda item: item.order):
        x, y = seed.cell
        if any(
            (neighbor := by_cell.get((x + dx, y + dy))) is not None and neighbor.order < seed.order
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if dx or dy
        ):
            continue
        if any(
            math.hypot(
                seed.hypothesis.east_m - other.hypothesis.east_m,
                seed.hypothesis.north_m - other.hypothesis.north_m,
            )
            < separation_m
            for other in selected
        ):
            continue
        selected.append(seed)
        if len(selected) == cap:
            break
    return selected


def verify_selected_plan(root: Path, selected: list[Seed]) -> None:
    pending = {seed.input_index: seed for seed in selected}
    with (root / "candidate-plan.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            seed = pending.get(row["input_index"])
            if seed is not None:
                if row["candidate_id"] != seed.candidate_id or tuple(row["position_xy_m"]) != (
                    seed.hypothesis.east_m,
                    seed.hypothesis.north_m,
                ):
                    raise ValueError("Selected seed differs from immutable candidate plan")
                del pending[row["input_index"]]
            if not pending:
                break
    if pending:
        raise ValueError("Selected seed absent from candidate plan")


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def check_space(path: Path) -> int:
    free = shutil.disk_usage(path).free
    if free < FLOOR + 5 * 1024**2:
        raise OSError("Disk reserve leaves less than 5 MiB for bounded output")
    return free


def _alarm(_signal: int, _frame: Any) -> None:
    raise TimeoutError("Whole diagnostic deadline exceeded")


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    check_space(args.output.parent)
    args.output.mkdir(exist_ok=False)
    rows_path = args.output / "refined-modes.jsonl"
    rows_path.touch()
    manifest: dict[str, Any] = {
        "schema": "cached-mode-refinement-v1",
        "source_attempt": str(args.attempt.resolve()),
        "harness_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "package_source": str(Path(__import__("bearing_localization").__file__).resolve()),
        "mode_cap": 64,
        "minimum_seed_separation_m": 100.0,
        "initial_step_m": 125.0,
        "max_iterations": 50,
        "deadline_s": args.timeout_s,
        "selection_rule": "saved-eight-neighbor-local-minima-score-xy-id-v1",
        "full_domain_complete": False,
        "decision_status": "ambiguous",
        "search_status": "budget_exhausted",
        "certificate_scope": "none",
        "rows_file": rows_path.name,
        "rows_order": "seed-selection-order",
        "completed_mode_count": 0,
        "status": "started",
    }
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_s)
    write_json(args.output / "manifest.json", manifest)
    try:
        with args.lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seeds, bindings = load_committed(args.attempt)
            manifest.update(bindings)
            supervisor = read_json(args.attempt / "supervisor-manifest.json")
            manifest["source_evidence_sha256"] = {
                key: supervisor[key]
                for key in (
                    "candidate_plan_sha256",
                    "candidate_inventory_sha256",
                    "candidate_dispositions_sha256",
                    "chunk_results_sha256",
                    "hypotheses_sha256",
                )
            }
            package_root = Path(manifest["package_source"]).parent.parent
            manifest["package_commit"] = subprocess.check_output(
                ["git", "-C", str(package_root), "rev-parse", "HEAD"], text=True
            ).strip()
            if manifest["package_commit"] != bindings["identity"]["scoring_source_sha"]:
                raise ValueError("Package source is not the frozen scoring commit")
            selected = select_modes(seeds)
            verify_selected_plan(args.attempt, selected)
            manifest["selected_mode_count"] = len(selected)
            manifest["mode_cap_reached"] = len(selected) == 64
            manifest["selected_modes"] = [
                {
                    "input_index": s.input_index,
                    "candidate_id": s.candidate_id,
                    "cached_hypothesis": asdict(s.hypothesis),
                    "chunk_name": s.chunk_name,
                    "chunk_sha256": s.chunk_sha256,
                    "sidecar_sha256": s.sidecar_sha256,
                }
                for s in selected
            ]
            identity = bindings["identity"]
            input_paths = {
                name: args.static / f"{name}.json" for name in ("measurements", "catalog", "map")
            }
            for name, path in input_paths.items():
                if sha256(path) != identity["static_input_sha256"][name]:
                    raise ValueError(f"Static input hash mismatch: {name}")
            if sha256(args.config) != identity["scoring_config_sha256"]:
                raise ValueError("Frozen config hash mismatch")
            manifest["input_paths"] = {k: str(v.resolve()) for k, v in input_paths.items()}
            manifest["config_path"] = str(args.config.resolve())
            config_data = read_json(args.config)
            if config_data.pop("contract_type", None) != "SearchConfig":
                raise ValueError("Configuration discriminator mismatch")
            config = SearchConfig(**config_data)
            measurements = read_contract(input_paths["measurements"], MeasurementSetV2)
            catalog = read_contract(input_paths["catalog"], CatalogV2)
            map_input = read_contract(input_paths["map"], MapV2)
            vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
            scorer = CandidateScorer(measurements, catalog, vector_map, config)
            manifest["setup_elapsed_s"] = time.perf_counter() - started
            write_json(args.output / "manifest.json", manifest)
            for order, seed in enumerate(selected):
                check_space(args.output)
                mode_start = time.perf_counter()
                exact = scorer.evaluate((seed.hypothesis.east_m, seed.hypothesis.north_m))
                if exact.hypothesis is None or not math.isclose(
                    exact.hypothesis.score, seed.hypothesis.score, rel_tol=1e-10, abs_tol=1e-8
                ):
                    raise ValueError(f"Seed objective revalidation failed: {seed.candidate_id}")
                result = refine_modes(
                    measurements,
                    catalog,
                    vector_map,
                    config,
                    [exact.hypothesis],
                    initial_step_m=125.0,
                    max_iterations=50,
                )
                mode = result.modes[0]
                row = {
                    "seed_order": order,
                    "input_index": seed.input_index,
                    "candidate_id": seed.candidate_id,
                    "chunk_name": seed.chunk_name,
                    "chunk_sha256": seed.chunk_sha256,
                    "sidecar_sha256": seed.sidecar_sha256,
                    "cached_hypothesis": asdict(seed.hypothesis),
                    "revalidated_hypothesis": asdict(exact.hypothesis),
                    "refined_mode": asdict(mode),
                    "mode_elapsed_s": time.perf_counter() - mode_start,
                }
                with rows_path.open("a") as stream:
                    stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                manifest["completed_mode_count"] = order + 1
                manifest["rows_sha256"] = sha256(rows_path)
                manifest["elapsed_s"] = time.perf_counter() - started
                write_json(args.output / "manifest.json", manifest)
                print(
                    json.dumps(
                        {
                            "completed_modes": order + 1,
                            "score": None if mode.hypothesis is None else mode.hypothesis.score,
                            "elapsed_s": manifest["elapsed_s"],
                        }
                    ),
                    flush=True,
                )
            manifest["status"] = "selected_modes_completed"
    except Exception as exc:
        manifest["status"] = "deadline_exhausted" if isinstance(exc, TimeoutError) else "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        manifest["elapsed_s"] = time.perf_counter() - started
        manifest["rows_sha256"] = sha256(rows_path)
        manifest["free_bytes_at_end"] = shutil.disk_usage(args.output).free
        write_json(args.output / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("attempt", "static", "config", "output", "lock"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error("--timeout-s must be finite and positive")
    result = run(args)
    print(
        json.dumps(
            {"status": result["status"], "completed_mode_count": result["completed_mode_count"]}
        )
    )
    return 0 if result["status"] == "selected_modes_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
