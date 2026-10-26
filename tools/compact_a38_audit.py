"""Losslessly archive and compact A38 physical identity audit maps."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from tools.a38_archive import atomic_write_bytes, verified_gzip_bytes, write_verified_gzip
from tools.run_accuracy_suite import _read_table, _write_table


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact_audits(root: Path, manifest: Path, split: str) -> int:
    count = 0
    manifest.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_table(manifest) if manifest.exists() else []
    indexed = {row["trial_id"]: row for row in existing}
    for path in sorted((root / split).glob("**/physical-identity-audit.json")):
        archive = path.with_name("physical-identity-audit.full.json.gz")
        prior = indexed.get(_trial_id(path, root, split))
        source = path.read_bytes()
        archived_raw = None
        if archive.exists():
            try:
                archived_raw = verified_gzip_bytes(
                    archive, prior.get("full_audit_sha256") if prior else None
                )
            except (OSError, EOFError, gzip.BadGzipFile, RuntimeError):
                if prior and sha(source) == prior.get("compact_audit_sha256"):
                    raise RuntimeError(
                        f"Full audit archive is incomplete and compact source cannot "
                        f"restore it: {archive}"
                    )
                if b"physical_entity_by_catalog_key" not in source:
                    raise RuntimeError(
                        f"Full audit archive is incomplete and full source is "
                        f"unavailable: {archive}"
                    )
                archive.unlink()
        if archived_raw is None:
            raw = source
            digest = sha(raw)
            doc = json.loads(raw)
            if not isinstance(doc.get("physical_entity_by_catalog_key"), dict):
                raise RuntimeError(f"Cannot recreate full audit archive from compact view: {path}")
            write_verified_gzip(raw, archive)
        else:
            raw = archived_raw
            digest = sha(raw)
            doc = json.loads(raw)
            if source != raw and prior and sha(source) != prior.get("compact_audit_sha256"):
                raise RuntimeError(
                    f"Audit source matches neither full archive nor compact manifest: {path}"
                )
        entity_map = doc.pop("physical_entity_by_catalog_key", None)
        unresolved = doc.pop("unresolved_catalog_keys", [])
        if not isinstance(entity_map, dict) or not isinstance(unresolved, list):
            raise ValueError(f"Unexpected identity audit layout: {path}")
        summary = dict(doc)
        summary["physical_entity_map_archived"] = True
        summary["physical_entity_map_entry_count"] = len(entity_map)
        summary["unresolved_catalog_key_count"] = len(unresolved)
        compact = (json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        if source != raw and prior is None and source != compact:
            raise RuntimeError(f"Orphan compact audit does not match its full archive: {path}")
        if source != compact:
            atomic_write_bytes(path, compact)
        trial_id = _trial_id(path, root, split)
        support_path = path.with_name("support.json")
        support = json.loads(support_path.read_text()) if support_path.exists() else {}
        audit_row = {
            "split": split,
            "trial_id": trial_id,
            "audit_path": str(path),
            "full_audit_archive_path": str(archive),
            "full_audit_sha256": digest,
            "full_audit_bytes": len(raw),
            "entity_map_entry_count": len(entity_map),
            "unresolved_key_count": len(unresolved),
            "compact_audit_sha256": sha(compact),
            "compact_audit_bytes": len(compact),
            "gzip_audit_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "gzip_audit_bytes": archive.stat().st_size,
            "gzip_roundtrip_sha256_verified": True,
            "catalog_sha256": summary.get("catalog_sha256", ""),
            "support_status": support.get("supported", ""),
        }
        previous = indexed.get(trial_id)
        if previous is None or any(
            str(previous.get(key, "")) != str(value) for key, value in audit_row.items()
        ):
            indexed[trial_id] = audit_row
            count += 1
    if count:
        _write_table(manifest, list(indexed.values()))
    return count


def _trial_id(path: Path, root: Path, split: str) -> str:
    parts = path.relative_to(root / split).parts
    if len(parts) < 6:
        raise ValueError(f"Unexpected identity audit path layout: {path}")
    range_m = parts[3].removesuffix("m")
    type_mode = "on" if parts[4] == "types-on" else "off"
    return f"{split}/{parts[0]}/{parts[1]}/{parts[2]}/range{range_m}m/types{type_mode}/{parts[5]}"
