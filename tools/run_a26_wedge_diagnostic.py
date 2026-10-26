"""Run the frozen one-case wedge-complete development diagnostic."""

from __future__ import annotations

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


def main() -> int:
    worktree = Path(sys.argv[1]).resolve()
    output_root = Path(sys.argv[2]).resolve()
    run_dir = output_root / "run"
    static_dir = output_root / "static-inputs"
    measurement = static_dir / "measurements.json"
    catalog = static_dir / "catalog.json"
    map_path = static_dir / "map.json"
    scope = static_dir / "scope-manifest.json"
    config = run_dir / "search-config.json"
    paths = {
        "catalog.json": catalog,
        "map.json": map_path,
        "measurements.json": measurement,
        "scope-manifest.json": scope,
    }
    actual = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    if actual != EXPECTED_SHA256:
        raise SystemExit(f"Frozen input hash mismatch: {actual}")

    sys.path.insert(0, str(worktree))
    from tools.run_accuracy_suite import _run_process

    argv = [
        "uv",
        "run",
        "bearing-localization-search",
        "--schema-version",
        "2",
        "--algorithm",
        "wedge_complete",
        "--max-grid-bearing-error-deg",
        "0.5",
        "--wedge-cell-size-m",
        "250",
        "--refine-modes",
        "8",
        "--refine-iterations",
        "50",
        "--refine-initial-step-m",
        "100",
        "--measurements",
        str(measurement),
        "--catalog",
        str(catalog),
        "--map",
        str(map_path),
        "--config",
        str(config),
        "--projected-crs",
        "EPSG:32618",
        "--scope-manifest",
        str(scope),
        "--output",
        str(run_dir / "estimate.json"),
    ]
    hashes = {
        **actual,
        "search-config.json": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    run = {
        "run_id": "a26-wedge-complete-07d8036-port001-placement001-r0-10km-typesoff-nominal",
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
        ).strip(),
        "started_utc": datetime.now(UTC).isoformat(),
        "timeout_s": 1800.0,
        "argv": argv,
        "input_sha256": hashes,
        "search_truth_input": None,
    }
    started = time.monotonic()
    result = _run_process(argv, cwd=worktree, timeout_s=1800.0)
    run.update(result)
    run["wall_s"] = time.monotonic() - started
    run["finished_utc"] = datetime.now(UTC).isoformat()
    (run_dir / "stdout.txt").write_text(result.get("stdout", ""))
    (run_dir / "stderr.txt").write_text(result.get("stderr", ""))
    (run_dir / "search-run.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: run.get(key) for key in ("status", "returncode", "wall_s")}))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
