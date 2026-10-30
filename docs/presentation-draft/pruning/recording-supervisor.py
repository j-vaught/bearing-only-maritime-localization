"""Run one approved pruning recording against saved inputs and caches."""

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import time

import numpy as np

REPO = Path("/home/jvaught2/projects/bearing-only-maritime-localization")
JOB = Path(__file__).resolve().parent
SOURCE = REPO / "runs/charleston-coast-distinct-marker-20261029T153236Z"
REFERENCE = REPO / "docs/verification/charleston-coastwide-distinct-result.json"
PYTHON = str(REPO / ".venv/bin/python3")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write(name, data):
    (JOB / name).write_text(json.dumps(data, indent=2) + "\n")


def status(stage, **extra):
    write(
        "status.json",
        {
            "stage": stage,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            **extra,
        },
    )


def snapshot():
    reference = json.loads(REFERENCE.read_text())
    paths = [Path(p) for p in reference["preservation"]["before"]]
    paths += [
        REFERENCE,
        SOURCE / "settings.json",
        SOURCE / "estimate-before-truth.json",
        SOURCE / "estimate-freeze.json",
        REPO / "docs/verification/charleston-coastwide-distinct-terminal-results.npz",
    ]
    paths += list((REPO / "maritime/search/gpu/resident").glob("*.py"))
    paths += list((REPO / "maritime/search/gpu/resident").glob("*.cu"))
    return {
        str(p.resolve()): {
            "size": p.stat().st_size,
            "mtime_ns": p.stat().st_mtime_ns,
            "sha256": digest(p),
        }
        for p in paths
    }


def sorted_cells(cells):
    return cells[np.lexsort(cells.T[::-1])]


def main():
    started = time.perf_counter()
    status("preserving_inputs_and_caches")
    before = snapshot()
    write("preservation-before.json", before)
    status("recording_gpu_pruning", devices=[0, 1, 2])
    with (JOB / "recording.log").open("wb") as log:
        result = subprocess.run(
            [
                PYTHON,
                "-m",
                "tools.record_resident_pruning",
                "--settings",
                str(JOB / "settings.json"),
                "--output",
                str(JOB / "recording"),
                "--cache-dir",
                str(REPO / ".cache/resident-world"),
                "--object-cache-dir",
                str(
                    (REPO / ".cache/resident-world").resolve().parent
                    / "resident-aid-objects"
                ),
                "--optimized",
                "--execute",
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if result.returncode:
        raise RuntimeError(
            f"Pruning recorder exited {result.returncode}. See recording.log"
        )
    status("checking_recorded_geometry")
    summary = json.loads((JOB / "recording/summary.json").read_text())
    with np.load(JOB / "recording/pruning-trace.npz", allow_pickle=False) as data:
        cells, state = data["cells"], data["state"]
        widths = np.max(cells[:, 2:] - cells[:, :2], axis=1)
        leaves = cells[(state == 0) & (widths <= 1)]
        parents = data["parent_id"]
        seed_ids = data["seed_root_id"]
        children = np.bincount(parents[parents >= 0], minlength=len(cells))
        hierarchy_ok = json.loads(data["manifest_utf8"].tobytes())["hierarchy_valid"]
        complete_roots = np.array_equal(np.sort(seed_ids), np.arange(39435))
        complete_children = np.array_equal(
            children, np.where((state == 0) & (widths > 1), 4, 0)
        )
    with np.load(
        REPO / "docs/verification/charleston-coastwide-distinct-terminal-results.npz",
        allow_pickle=False,
    ) as data:
        frozen_leaves = data["cells"]
    parity = {
        "evaluated": len(cells),
        "pruned": int(np.count_nonzero(state)),
        "expanded": int(np.count_nonzero((state == 0) & (widths > 1))),
        "terminal": len(leaves),
        "hierarchy_complete": bool(hierarchy_ok),
        "all_seed_roots_exactly_once": bool(complete_roots),
        "all_expansions_four_children": bool(complete_children),
        "completed_roots": summary["completed_roots"],
        "summary_complete": summary["complete"],
        "terminal_rectangles_bitwise_identical": bool(
            np.array_equal(sorted_cells(leaves), sorted_cells(frozen_leaves))
        ),
        "input_hashes_identical": json.loads(
            (JOB / "recording/input-hashes.json").read_text()
        )
        == json.loads((SOURCE / "search/input-hashes.json").read_text()),
        "base_cache_hit": json.loads(
            (JOB / "recording/prepared-cache.json").read_text()
        )["hit"],
        "canonical_cache_hit": json.loads(
            (JOB / "recording/canonical-object-cache.json").read_text()
        )["hit"],
        "source_run": str(SOURCE),
        "trace_sha256": digest(JOB / "recording/pruning-trace.npz"),
        "terminal_scoring_executed": False,
        "recording_wall_s": summary["elapsed_s"],
        "timing_scope": "Instrumented pruning-only recording. Not the saved search API benchmark.",
        "states": {
            str(int(k)): int(v)
            for k, v in zip(*np.unique(state, return_counts=True), strict=True)
        },
    }
    after = snapshot()
    write("preservation-after.json", after)
    parity["preserved_inputs_caches_numerical_source"] = before == after
    checks = [
        parity[k] == v
        for k, v in {
            "evaluated": 64875,
            "pruned": 58118,
            "expanded": 6360,
            "terminal": 397,
            "completed_roots": 39435,
        }.items()
    ]
    checks += [
        parity[k]
        for k in [
            "summary_complete",
            "terminal_rectangles_bitwise_identical",
            "input_hashes_identical",
            "base_cache_hit",
            "canonical_cache_hit",
            "preserved_inputs_caches_numerical_source",
            "hierarchy_complete",
            "all_seed_roots_exactly_once",
            "all_expansions_four_children",
        ]
    ]
    parity["passed"] = all(checks)
    write("parity.json", parity)
    if not parity["passed"]:
        raise RuntimeError(
            "Current recording differs from the frozen traversal or preservation checks."
        )
    status(
        "complete",
        parity_passed=True,
        total_recording_pipeline_s=time.perf_counter() - started,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        status("failed", error=str(error))
        raise
