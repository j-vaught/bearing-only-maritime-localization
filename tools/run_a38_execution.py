"""A38 batched preparation wrapper and lossless evaluator-truth compactor."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bearing_localization.evaluation.metrics import SupportManifest, evaluate_localization
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
)
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject
from tools.a38_archive import (
    atomic_write_bytes,
    verified_gzip_bytes,
    write_verified_gzip,
)
from tools.run_accuracy_suite import (
    MAX_PLACEMENTS,
    _extend_nominal_plan_if_needed,
    _read_table,
    _write_table,
    prepare_inputs,
)


def hbytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hfile(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluator_parity() -> dict[str, Any]:
    full = EvaluationTruth(
        100.0,
        200.0,
        30.0,
        (
            TrueObject("truth-object", "aid", 101.0, 201.0, observation_index=0),
            TrueObject("truth-group-member", "aid", 102.0, 202.0, observation_index=0),
            TrueObject("unobserved-object", "aid", 500.0, 600.0),
            TrueObject("second-unobserved", "vessel", 700.0, 800.0),
        ),
        trial_id="a38-parity",
    )
    compact = EvaluationTruth(
        full.observer_east_m,
        full.observer_north_m,
        full.observer_heading_deg,
        tuple(x for x in full.objects if x.observation_index is not None),
        trial_id=full.trial_id,
    )
    support = SupportManifest(True, 3, "port-parity", "placement-parity")
    cases = {
        "accepted_correct": LocalizationResult(
            (Hypothesis(100.0, 200.0, 0.0, (Assignment(0, "truth-object"),)),),
            0.01,
            "accepted",
            "completed",
            accepted_index=0,
            report_count=1,
        ),
        "accepted_wrong": LocalizationResult(
            (Hypothesis(100.0, 200.0, 0.0, (Assignment(0, "wrong-object"),)),),
            0.01,
            "accepted",
            "completed",
            accepted_index=0,
            report_count=1,
        ),
        "ambiguous": LocalizationResult(
            (Hypothesis(100.0, 200.0, 0.0),), 0.01, "ambiguous", "completed", report_count=1
        ),
        "no_fix": LocalizationResult((), 0.01, "no_fix", "completed", report_count=1),
        "failed": LocalizationResult((), 0.01, "no_fix", "failed", report_count=1),
    }
    for name, result in cases.items():
        if asdict(evaluate_localization(result, full, support)) != asdict(
            evaluate_localization(result, compact, support)
        ):
            raise AssertionError(f"Full/compact evaluator parity failed: {name}")
    return {
        "cases": list(cases),
        "support_fields_preserved": True,
        "all_metrics_equal": True,
        "full_objects": len(full.objects),
        "compact_objects": len(compact.objects),
    }


def compact_truth(path: Path, manifest: Path, split: str) -> dict[str, Any] | None:
    archive = path.with_name("truth.full.json.gz")
    manifest_rows = _read_table(manifest) if manifest.exists() else []
    indexed = {row["trial_id"]: row for row in manifest_rows}
    source = path.read_bytes() if path.exists() else None
    prior = next((row for row in manifest_rows if row.get("truth_path") == str(path)), None)

    archived_raw: bytes | None = None
    if archive.exists():
        try:
            archived_raw = verified_gzip_bytes(
                archive, prior.get("full_truth_sha256") if prior else None
            )
        except (OSError, EOFError, gzip.BadGzipFile, RuntimeError):
            if source is None or (prior and hbytes(source) == prior.get("compact_truth_sha256")):
                raise RuntimeError(
                    f"Full truth archive is incomplete and no full source remains "
                    f"for recovery: {archive}"
                )
            archive.unlink()

    if archived_raw is None:
        if source is None:
            raise FileNotFoundError(f"Neither full truth source nor archive exists: {path}")
        doc = json.loads(source)
        objects = doc.get("objects")
        if not isinstance(objects, list):
            raise ValueError(f"Truth objects is not a list: {path}")
        full_raw = source
        full_sha = hbytes(full_raw)
        write_verified_gzip(full_raw, archive)
    else:
        full_raw = archived_raw
        full_sha = hbytes(full_raw)
        doc = json.loads(full_raw)
        objects = doc.get("objects")
        if not isinstance(objects, list):
            raise ValueError(f"Archived truth objects is not a list: {archive}")
        if source is not None:
            source_sha = hbytes(source)
            if prior and source_sha == prior.get("compact_truth_sha256"):
                pass
            elif source != full_raw:
                if prior is not None:
                    raise RuntimeError(
                        f"Truth source matches neither full archive nor compact manifest: {path}"
                    )
                keep_from_archive = [
                    obj for obj in objects if obj.get("observation_index") is not None
                ]
                compact_from_archive = {
                    key: value for key, value in doc.items() if key != "objects"
                }
                compact_from_archive["objects"] = keep_from_archive
                expected_compact = (
                    json.dumps(compact_from_archive, indent=2, sort_keys=True, allow_nan=False)
                    + "\n"
                ).encode()
                if source != expected_compact:
                    raise RuntimeError(
                        f"Orphan compact truth does not match its full archive: {path}"
                    )

    keep = [obj for obj in objects if obj.get("observation_index") is not None]
    compact = {key: value for key, value in doc.items() if key != "objects"}
    compact["objects"] = keep
    compact_bytes = (json.dumps(compact, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if source != compact_bytes:
        atomic_write_bytes(path, compact_bytes)
    trial_id = str(doc.get("trial_id", ""))
    if not trial_id:
        raise ValueError(f"Truth document has no trial_id: {path}")
    row = {
        "split": split,
        "trial_id": trial_id,
        "truth_path": str(path),
        "full_truth_archive_path": str(archive),
        "full_truth_sha256": full_sha,
        "full_truth_bytes": len(full_raw),
        "full_object_count": len(objects),
        "retained_observation_object_count": len(keep),
        "compact_truth_sha256": hbytes(compact_bytes),
        "compact_truth_bytes": len(compact_bytes),
        "gzip_full_truth_sha256": hfile(archive),
        "gzip_full_truth_bytes": archive.stat().st_size,
        "gzip_roundtrip_sha256_verified": True,
        "view": "observer pose plus objects referenced by evaluator true_by_group",
    }
    previous = indexed.get(trial_id)
    if (
        previous is not None
        and source == compact_bytes
        and all(str(previous.get(key, "")) == str(value) for key, value in row.items())
    ):
        return None
    indexed[trial_id] = row
    manifest.parent.mkdir(parents=True, exist_ok=True)
    _write_table(manifest, list(indexed.values()))
    return row


def compact_split(root: Path, manifest: Path, split: str) -> int:
    n = 0
    for path in sorted((root / split).glob("**/truth.json")):
        n += compact_truth(path, manifest, split) is not None
    from tools.compact_a38_audit import compact_audits

    compact_audits(root, manifest.with_name("identity-audit-compaction.csv"), split)
    return n


def folder(root: Path, split: str, row: dict[str, str]) -> Path:
    label = "types-on" if row["report_types"] == "True" else "types-off"
    return (
        root
        / split
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{row['range_m']}m"
        / label
        / row["sensor_condition"]
    )


def prepare_batches(
    plan: Path, catalog: Path, maps: Path, root: Path, split: str, manifest: Path, batch_size: int
) -> dict[str, Any]:
    parity = evaluator_parity()
    config = json.loads(plan.read_text())
    table_path = plan.parent / config["trial_table"]
    rows = _read_table(table_path)
    selected = [r for r in rows if r["split"] == split]
    prep = root / split / "preparation.csv"
    prior = {r["trial_id"]: r for r in _read_table(prep)} if prep.exists() else {}
    compact_split(root, manifest, split)
    files = (
        "measurements.json",
        "truth.json",
        "diagnostics.json",
        "support.json",
        "difficulty.json",
        "physical-identity-audit.json",
    )
    pending = [
        r["trial_id"]
        for r in selected
        if prior.get(r["trial_id"], {}).get("status") != "completed"
        or not all((folder(root, split, r) / name).is_file() for name in files)
    ]
    batches = 0
    for start in range(0, len(pending), batch_size):
        ids = set(pending[start : start + batch_size])
        prepare_inputs(
            plan,
            catalog_path=catalog,
            map_cache=maps,
            input_root=root,
            split=split,
            placements_per_port=25,
            only_trial_ids=ids,
            _allow_extension=False,
        )
        compact_split(root, manifest, split)
        batches += 1
    extension_sizes = []
    while True:
        added = _extend_nominal_plan_if_needed(plan, input_root=root, split=split)
        if not added:
            break
        extension_sizes.append(len(added))
        for start in range(0, len(added), batch_size):
            ids = set(added[start : start + batch_size])
            prepare_inputs(
                plan,
                catalog_path=catalog,
                map_cache=maps,
                input_root=root,
                split=split,
                placements_per_port=MAX_PLACEMENTS,
                only_trial_ids=ids,
                _allow_extension=False,
            )
            compact_split(root, manifest, split)
            batches += 1
    latest = json.loads(plan.read_text())
    all_rows = _read_table(plan.parent / latest["trial_table"])
    attempt_rows = _read_table(prep) if prep.exists() else []
    return {
        "split": split,
        "trial_rows": sum(r["split"] == split for r in all_rows),
        "initial_rows_attempted": len(pending),
        "preparation_attempt_rows": len(attempt_rows),
        "noncompleted_attempt_rows": sum(r["status"] != "completed" for r in attempt_rows),
        "batches": batches,
        "extension_batch_row_counts": extension_sizes,
        "evaluator_parity": parity,
        "truth_manifest": str(manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)
    cp = subs.add_parser("compact")
    cp.add_argument("--input-root", type=Path, required=True)
    cp.add_argument("--split", choices=("development", "validation"), required=True)
    cp.add_argument("--manifest", type=Path, required=True)
    pp = subs.add_parser("prepare")
    for name in ("plan", "catalog", "map-cache", "input-root", "manifest"):
        pp.add_argument("--" + name, type=Path, required=True)
    pp.add_argument("--split", choices=("development", "validation"), required=True)
    pp.add_argument("--batch-size", type=int, default=10)
    a = parser.parse_args()
    if a.cmd == "compact":
        parity = evaluator_parity()
        print(
            json.dumps(
                {
                    "split": a.split,
                    "newly_compacted": compact_split(a.input_root, a.manifest, a.split),
                    "evaluator_parity": parity,
                },
                sort_keys=True,
            )
        )
        return 0
    if a.batch_size < 1:
        parser.error("--batch-size must be positive")
    print(
        json.dumps(
            prepare_batches(
                a.plan, a.catalog, a.map_cache, a.input_root, a.split, a.manifest, a.batch_size
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
