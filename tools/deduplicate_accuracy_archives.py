#!/usr/bin/env python3
"""Plan and apply exact-content hardlink deduplication for completed evidence files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1024 * 1024
PLAN_VERSION = 1


def _signature(path: Path) -> dict[str, int]:
    st = path.lstat()
    return {
        "dev": st.st_dev,
        "ino": st.st_ino,
        "mode": stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
        "nlink": st.st_nlink,
        "blocks": getattr(st, "st_blocks", 0),
    }


def _matches_live_signature(path: Path, expected: dict[str, int]) -> bool:
    current = _signature(path)
    return all(
        current[key] == expected[key]
        for key in ("dev", "ino", "mode", "uid", "gid", "size", "mtime_ns")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(CHUNK_SIZE):
                digest.update(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in keys):
        raise RuntimeError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def _trial_relative_path(trial_id: str) -> Path:
    parts = trial_id.split("/")
    if parts and parts[0] == "development":
        parts = parts[1:]
    normalized = []
    for part in parts:
        if part.startswith("range") and part.endswith("m"):
            part = part.removeprefix("range")
        if part == "typeson":
            part = "types-on"
        elif part == "typesoff":
            part = "types-off"
        normalized.append(part)
    if not normalized or any(part in {"", ".", ".."} for part in normalized):
        raise ValueError(f"invalid trial id: {trial_id!r}")
    return Path(*normalized)


def _load_completed_trials(path: Path) -> set[str]:
    completed: set[str] = set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not {"trial_id", "status"}.issubset(reader.fieldnames or set()):
            raise ValueError("completion manifest requires trial_id and status columns")
        for row in reader:
            if row["status"] == "completed":
                completed.add(_trial_relative_path(row["trial_id"]).as_posix())
    return completed


def _iter_regular_files(root: Path) -> tuple[list[Path], list[Path]]:
    regular: list[Path] = []
    symlinks: list[Path] = []
    for path in root.rglob("*"):
        try:
            st = path.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"tree changed during inventory: {path}") from exc
        if stat.S_ISLNK(st.st_mode):
            symlinks.append(path)
        elif stat.S_ISREG(st.st_mode):
            regular.append(path)
    return sorted(regular), sorted(symlinks)


def inventory(root: Path, completion_manifest: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve(strict=True)
    completed = _load_completed_trials(completion_manifest)
    files, symlinks = _iter_regular_files(root)
    categories: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "apparent_bytes": 0, "allocated_bytes": 0, "nlink_gt1": 0}
    )
    file_records: dict[str, dict[str, Any]] = {}
    groups: dict[tuple[int, str], list[str]] = defaultdict(list)
    for path in files:
        signature = _signature(path)
        relative = path.relative_to(root)
        category = path.suffix.lower() or "[noext]"
        row = categories[category]
        row["count"] += 1
        row["apparent_bytes"] += signature["size"]
        row["allocated_bytes"] += signature["blocks"] * 512
        row["nlink_gt1"] += int(signature["nlink"] > 1)
        relative_text = relative.as_posix()
        is_completed = any(
            relative_text == trial_dir or relative_text.startswith(trial_dir + "/")
            for trial_dir in completed
        )
        digest = _sha256(path)
        file_records[relative_text] = {
            "relative_path": relative_text,
            "sha256": digest,
            "signature": signature,
            "completed": is_completed,
        }
        if is_completed:
            groups[(signature["size"], digest)].append(relative_text)

    duplicate_groups: list[dict[str, Any]] = []
    for (size, digest), paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        ordered = sorted(paths)
        canonical = ordered[0]
        canonical_record = file_records[canonical]
        duplicates = []
        for duplicate in ordered[1:]:
            duplicate_record = file_records[duplicate]
            if duplicate_record["signature"]["ino"] == canonical_record["signature"]["ino"]:
                continue
            left = canonical_record["signature"]
            right = duplicate_record["signature"]
            eligible = all(left[key] == right[key] for key in ("dev", "mode", "uid", "gid"))
            duplicates.append(
                {
                    "path": duplicate,
                    "sha256": digest,
                    "size": size,
                    "signature": right,
                    "eligible": eligible,
                    "reason": None if eligible else "filesystem-or-mode-metadata-differs",
                }
            )
        if duplicates:
            duplicate_groups.append(
                {
                    "canonical": canonical,
                    "canonical_signature": canonical_record["signature"],
                    "sha256": digest,
                    "size": size,
                    "duplicates": duplicates,
                }
            )

    snapshot = {
        "version": PLAN_VERSION,
        "root": str(root),
        "completion_manifest": str(completion_manifest.resolve()),
        "completed_trial_count": len(completed),
        "files": file_records,
        "symlinks": [path.relative_to(root).as_posix() for path in symlinks],
        "duplicate_groups": duplicate_groups,
        "categories": dict(sorted(categories.items())),
    }
    summary = {
        "regular_file_count": len(files),
        "symlink_count": len(symlinks),
        "completed_trial_count": len(completed),
        "category_totals": dict(sorted(categories.items())),
        "duplicate_group_count": len(duplicate_groups),
        "eligible_duplicate_count": sum(
            item["eligible"] for group in duplicate_groups for item in group["duplicates"]
        ),
        "estimated_allocated_bytes_reclaimable": sum(
            item["signature"]["blocks"] * 512
            for group in duplicate_groups
            for item in group["duplicates"]
            if item["eligible"]
        ),
    }
    return snapshot, summary


def _validate_snapshot(plan: dict[str, Any]) -> None:
    root = Path(plan["root"]).resolve(strict=True)
    current_files, symlinks = _iter_regular_files(root)
    current_relatives = {path.relative_to(root).as_posix() for path in current_files}
    planned_relatives = set(plan["files"])
    if current_relatives != planned_relatives:
        added = sorted(current_relatives - planned_relatives)
        missing = sorted(planned_relatives - current_relatives)
        raise RuntimeError(f"file set changed since plan; added={added[:3]}, missing={missing[:3]}")
    if symlinks or plan.get("symlinks"):
        raise RuntimeError("symlink detected; refusing apply")
    for path in current_files:
        relative = path.relative_to(root).as_posix()
        record = plan["files"][relative]
        if _signature(path) != record["signature"]:
            raise RuntimeError(f"file metadata changed since plan: {relative}")
        if _sha256(path) != record["sha256"]:
            raise RuntimeError(f"file content changed since plan: {relative}")


def apply_plan(plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("version") != PLAN_VERSION:
        raise ValueError("unsupported plan version")
    _validate_snapshot(plan)
    root = Path(plan["root"]).resolve(strict=True)
    operations = [
        (group, duplicate)
        for group in plan["duplicate_groups"]
        for duplicate in group["duplicates"]
        if duplicate["eligible"]
    ]
    applied: list[tuple[Path, Path]] = []
    try:
        for group, duplicate in operations:
            canonical = root / group["canonical"]
            destination = root / duplicate["path"]
            if not _matches_live_signature(canonical, group["canonical_signature"]):
                raise RuntimeError(f"canonical file changed before replacement: {canonical}")
            if not _matches_live_signature(destination, duplicate["signature"]):
                raise RuntimeError(f"duplicate file changed before replacement: {destination}")
            temp_link: Path | None = None
            backup_link: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, prefix=".a41-link-", delete=False
                ) as temp:
                    temp_link = Path(temp.name)
                temp_link.unlink()
                os.link(canonical, temp_link, follow_symlinks=False)
                backup_link = destination.with_name(f".{destination.name}.a41-backup")
                if backup_link.exists():
                    raise RuntimeError(f"rollback path already exists: {backup_link}")
                os.link(destination, backup_link, follow_symlinks=False)
                os.replace(temp_link, destination)
                temp_link = None
                applied.append((destination, backup_link))
                backup_link = None
                if _signature(destination)["ino"] != _signature(canonical)["ino"]:
                    raise RuntimeError(f"hardlink verification failed: {destination}")
            finally:
                if temp_link is not None:
                    temp_link.unlink(missing_ok=True)
                if backup_link is not None:
                    backup_link.unlink(missing_ok=True)
        for _, backup in applied:
            backup.unlink()
    except Exception:
        for destination, backup in reversed(applied):
            if backup.exists():
                os.replace(backup, destination)
        raise
    return {
        "applied_replacements": len(applied),
        "bytes_reclaimed_estimate": sum(
            duplicate["signature"]["blocks"] * 512
            for group in plan["duplicate_groups"]
            for duplicate in group["duplicates"]
            if duplicate["eligible"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--completion-manifest", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--apply-manifest", type=Path)
    args = parser.parse_args()
    if args.apply_manifest:
        result = apply_plan(args.apply_manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    plan, summary = inventory(args.root, args.completion_manifest)
    output = args.manifest_out or Path("a41-dedup-plan.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"dry-run manifest: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
