"""Execution-only isolated-worker wrapper for deterministic A38 input preparation."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.run_a38_execution import compact_split, folder
from tools.run_accuracy_suite import _read_table, _write_table, prepare_inputs

ROOT = Path("/home/jvaught2/projects/bearing-only-maritime-localization")
A38 = ROOT / ".agent-work/outputs/a38"


def _worker(
    args: tuple[str, str, str, str, list[str], int, list[dict[str, str]]],
) -> dict[str, Any]:
    plan_s, catalog_s, maps_s, temp_s, ids, worker_index, prior_attempts = args
    temp_root = Path(temp_s)
    plan = Path(plan_s)
    if prior_attempts:
        _write_table(temp_root / "development/preparation-attempts.csv", prior_attempts)
    prepare_inputs(
        plan,
        catalog_path=Path(catalog_s),
        map_cache=Path(maps_s),
        input_root=temp_root,
        split="development",
        placements_per_port=25,
        only_trial_ids=set(ids),
        _allow_extension=False,
    )
    manifest = temp_root / f"worker-{worker_index}-truth-compaction.csv"
    compact_split(temp_root, manifest, "development")
    rows = _read_table(temp_root / "development/preparation.csv")
    return {"root": str(temp_root), "rows": len(rows), "trial_ids": ids}


def _merge_worker(src: Path, dest: Path, truth_manifest: Path, audit_manifest: Path) -> int:
    split = "development"
    src_split = src / split
    dest_split = dest / split
    copied = 0
    moved_output_paths: set[str] = set()
    for directory in sorted(p for p in src_split.glob("port-*/*/r*/*m/*/*") if p.is_dir()):
        rel = directory.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Refusing to merge over existing trial directory {target}")
        shutil.move(str(directory), str(target))
        copied += 1
        moved_output_paths.add(rel.as_posix())
    merged_preparation_rows: list[dict[str, str]] = []
    for filename in (
        "preparation.csv",
        "preparation-attempts.csv",
        "placement-sampling-attempts.csv",
    ):
        source = src_split / filename
        if not source.is_file():
            continue
        target = dest_split / filename
        old = _read_table(target) if target.exists() else []
        new = _read_table(source)
        if filename == "preparation.csv":
            merged_preparation_rows = new
            indexed = {r["trial_id"]: r for r in old}
            indexed.update({r["trial_id"]: r for r in new})
        else:
            indexed = {r["attempt_id"]: r for r in old}
            for row in new:
                if row["attempt_id"] in indexed and indexed[row["attempt_id"]] != row:
                    raise ValueError(f"Conflicting merged attempt {row['attempt_id']}")
                indexed[row["attempt_id"]] = row
        _write_table(target, list(indexed.values()))
    if not merged_preparation_rows:
        raise RuntimeError(f"Worker {src} did not produce preparation rows")
    completed_rows = [row for row in merged_preparation_rows if row.get("status") == "completed"]
    expected_output_paths = {
        folder(src, split, row).relative_to(src).as_posix() for row in completed_rows
    }
    if moved_output_paths != expected_output_paths:
        missing = sorted(expected_output_paths - moved_output_paths)
        unexpected = sorted(moved_output_paths - expected_output_paths)
        raise RuntimeError(
            f"Worker output directory mismatch; missing completed={missing}, "
            f"unexpected={unexpected}"
        )
    for filename, target in (
        ("worker-0-truth-compaction.csv", truth_manifest),
        ("identity-audit-compaction.csv", audit_manifest),
    ):
        source = src / filename
        if not source.is_file():
            continue
        old = _read_table(target) if target.exists() else []
        indexed = {r["trial_id"]: r for r in old}
        for row in _read_table(source):
            if row["trial_id"] in indexed:
                raise ValueError(f"Duplicate compact-manifest trial {row['trial_id']}")
            for field in (
                "truth_path",
                "full_truth_archive_path",
                "audit_path",
                "full_audit_archive_path",
            ):
                if field in row and row[field]:
                    row[field] = str(dest / split / Path(row[field]).relative_to(src / split))
            indexed[row["trial_id"]] = row
        _write_table(target, list(indexed.values()))
    # Failed attempts are valid outcomes without trial directories. Count
    # accounted attempt rows while still requiring every completed row's output.
    return len(merged_preparation_rows)


def validate_worker_ids(expected: list[str], observed: list[str]) -> None:
    if len(observed) != len(set(observed)):
        raise RuntimeError("Worker completion set contains duplicate trial IDs")
    if sorted(observed) != sorted(expected):
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected))
        raise RuntimeError(
            f"Worker completion set mismatch; missing={missing}, unexpected={unexpected}"
        )


def estimated_wave_gib(row_count: int) -> float:
    return row_count * 30_000_000 * 1.35 / (1024**3)


def enforce_disk_guard(free_gib: float, row_count: int, minimum_free_gib: float = 40) -> float:
    required = minimum_free_gib + estimated_wave_gib(row_count)
    if free_gib < required:
        raise RuntimeError(
            f"Disk guard requires {required:.2f} GiB free, observed {free_gib:.2f} GiB"
        )
    return required


def _free_gib(path: Path) -> float:
    stat = shutil.disk_usage(path)
    return stat.free / (1024**3)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--map-cache", type=Path, required=True)
    p.add_argument("--input-root", type=Path, required=True)
    p.add_argument("--truth-manifest", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--chunk-size", type=int, default=50)
    p.add_argument(
        "--max-rows", type=int, default=0, help="optional execution cap for a controlled replay"
    )
    p.add_argument(
        "--trial-id",
        action="append",
        default=[],
        help="prepare only the named trial ID; repeatable",
    )
    p.add_argument("--minimum-free-gib", type=float, default=40)
    a = p.parse_args()
    if a.workers < 1 or a.chunk_size < 1:
        p.error("workers and chunk-size must be positive")
    a.input_root.mkdir(parents=True, exist_ok=True)
    plan_doc = json.loads(a.plan.read_text())
    rows = _read_table(a.plan.parent / plan_doc["trial_table"])
    selected = [r for r in rows if r["split"] == "development"]
    prep_path = a.input_root / "development/preparation.csv"
    prior = {r["trial_id"]: r for r in _read_table(prep_path)} if prep_path.exists() else {}
    required = (
        "measurements.json",
        "truth.json",
        "diagnostics.json",
        "support.json",
        "difficulty.json",
        "physical-identity-audit.json",
    )
    pending = [
        r
        for r in selected
        if prior.get(r["trial_id"], {}).get("status") != "completed"
        or not all((folder(a.input_root, "development", r) / name).is_file() for name in required)
    ]
    if a.trial_id:
        selected_ids = set(a.trial_id)
        pending = [r for r in selected if r["trial_id"] in selected_ids]
        if len(pending) != len(selected_ids):
            raise ValueError(
                "One or more requested trial IDs are absent or already complete in this input root"
            )
    elif a.max_rows:
        pending = pending[: a.max_rows]
    print(
        json.dumps(
            {
                "pending_rows": len(pending),
                "workers": a.workers,
                "chunk_size": a.chunk_size,
                "free_gib": _free_gib(a.input_root),
            }
        ),
        flush=True,
    )
    for wave_start in range(0, len(pending), a.workers * a.chunk_size):
        wave = pending[wave_start : wave_start + a.workers * a.chunk_size]
        chunks = [wave[i : i + a.chunk_size] for i in range(0, len(wave), a.chunk_size)]
        # Six raw JSON products include the full truth and full identity map. Apply 35% headroom.
        free = _free_gib(a.input_root)
        try:
            required = enforce_disk_guard(free, len(wave), a.minimum_free_gib)
        except RuntimeError as exc:
            raise RuntimeError(
                f"Disk guard stopped wave at rows {wave_start}:{wave_start + len(wave)}; {exc}"
            ) from exc
        with tempfile.TemporaryDirectory(
            prefix="a38-wave-", dir=str(a.input_root.parent)
        ) as temp_base:
            worker_args = []
            roots = []
            for ix, chunk in enumerate(chunks):
                root = Path(temp_base) / f"worker-{ix}"
                roots.append(root)
                root.mkdir()
                chunk_ids = {r["trial_id"] for r in chunk}
                all_attempts_path = a.input_root / "development/preparation-attempts.csv"
                existing_attempts = (
                    _read_table(all_attempts_path) if all_attempts_path.exists() else []
                )
                prior_attempts = [r for r in existing_attempts if r["trial_id"] in chunk_ids]
                worker_args.append(
                    (
                        str(a.plan),
                        str(a.catalog),
                        str(a.map_cache),
                        str(root),
                        [r["trial_id"] for r in chunk],
                        ix,
                        prior_attempts,
                    )
                )
            with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
                futures = [pool.submit(_worker, args) for args in worker_args]
                results = [f.result() for f in as_completed(futures)]
            observed_ids = [trial for result in results for trial in result["trial_ids"]]
            expected_ids = [r["trial_id"] for r in wave]
            validate_worker_ids(expected_ids, observed_ids)
            moved = 0
            for root in roots:
                # Normalize per-worker truth manifest name for the merge helper.
                worker_truth = root / "worker-0-truth-compaction.csv"
                candidate = next(root.glob("worker-*-truth-compaction.csv"), None)
                if candidate and candidate != worker_truth:
                    candidate.rename(worker_truth)
                moved += _merge_worker(
                    root,
                    a.input_root,
                    a.truth_manifest,
                    a.truth_manifest.with_name("identity-audit-compaction.csv"),
                )
            if moved != len(wave):
                raise RuntimeError(f"Merged {moved} trial directories for {len(wave)} rows")
        print(
            json.dumps(
                {
                    "wave_start": wave_start,
                    "wave_rows": len(wave),
                    "merged_rows": moved,
                    "free_gib_after": _free_gib(a.input_root),
                }
            ),
            flush=True,
        )
    print(json.dumps({"status": "completed", "prepared_rows": len(pending)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
