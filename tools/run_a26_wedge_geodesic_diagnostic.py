"""Run the frozen one-case wedge-geodesic development diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_SHA256 = {
    "catalog.json": "2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704",
    "map.json": "cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93",
    "measurements.json": "ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1",
    "scope-manifest.json": "d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    worktree = args.worktree.resolve()
    output_root = args.output_root.resolve()
    run_dir = output_root / "run"
    static_dir = output_root / "static-inputs"
    paths = {
        "catalog.json": static_dir / "catalog.json",
        "map.json": static_dir / "map.json",
        "measurements.json": static_dir / "measurements.json",
        "scope-manifest.json": static_dir / "scope-manifest.json",
    }
    actual = {name: sha256(path) for name, path in paths.items()}
    if actual != EXPECTED_SHA256:
        raise SystemExit(f"Frozen input hash mismatch: {actual}")
    config = run_dir / "search-config.json"
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
        "--refine-modes",
        "8",
        "--refine-iterations",
        "50",
        "--refine-initial-step-m",
        "100",
        "--measurements",
        str(paths["measurements.json"]),
        "--catalog",
        str(paths["catalog.json"]),
        "--map",
        str(paths["map.json"]),
        "--config",
        str(config),
        "--projected-crs",
        "EPSG:32618",
        "--scope-manifest",
        str(paths["scope-manifest.json"]),
        "--output",
        str(run_dir / "estimate.json"),
        "--telemetry-output",
        str(run_dir / "estimate.telemetry.json"),
    ]
    hashes = {**actual, "search-config.json": sha256(config)}
    run = {
        "run_id": "a26-wedge-geodesic-port001-placement001-r0-10km-typesoff-nominal",
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
        ).strip(),
        "started_utc": datetime.now(UTC).isoformat(),
        "timeout_s": 1800.0,
        "argv": argv,
        "input_sha256": hashes,
        "search_truth_input": None,
        "execution_protocol": "new-session process group; TERM then KILL after grace on timeout",
    }
    sys.path.insert(0, str(worktree))
    from tools.run_accuracy_suite import _run_process

    started = time.monotonic()
    result = _run_process(argv, cwd=worktree, timeout_s=1800.0)
    run.update(result)
    run["external_wall_s"] = time.monotonic() - started
    run["finished_utc"] = datetime.now(UTC).isoformat()
    (run_dir / "stdout.txt").write_text(result.get("stdout", ""))
    (run_dir / "stderr.txt").write_text(result.get("stderr", ""))
    (run_dir / "command-manifest.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
