"""Losslessly archive and compact A38 physical identity audit maps."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from tools.run_accuracy_suite import _read_table, _write_table


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact_audits(root: Path, manifest: Path, split: str) -> int:
    count = 0
    manifest.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_table(manifest) if manifest.exists() else []
    indexed = {row["audit_path"]: row for row in existing}
    for path in sorted((root / split).glob("**/physical-identity-audit.json")):
        archive = path.with_name("physical-identity-audit.full.json.gz")
        if archive.exists():
            continue
        raw = path.read_bytes()
        digest = sha(raw)
        doc = json.loads(raw)
        entity_map = doc.pop("physical_entity_by_catalog_key", None)
        unresolved = doc.pop("unresolved_catalog_keys", [])
        if not isinstance(entity_map, dict) or not isinstance(unresolved, list):
            raise ValueError(f"Unexpected identity audit layout: {path}")
        with archive.open("wb") as dst:
            with gzip.GzipFile(fileobj=dst, mode="wb", compresslevel=1, mtime=0) as gz:
                gz.write(raw)
        replay = gzip.decompress(archive.read_bytes())
        if replay != raw or sha(replay) != digest:
            archive.unlink(missing_ok=True)
            raise RuntimeError(f"Audit gzip failed exact SHA-256 replay: {path}")
        summary = dict(doc)
        summary["physical_entity_map_archived"] = True
        summary["physical_entity_map_entry_count"] = len(entity_map)
        summary["unresolved_catalog_key_count"] = len(unresolved)
        compact = (json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        path.write_bytes(compact)
        parts = path.relative_to(root / split).parts
        if len(parts) < 6:
            raise ValueError(f"Unexpected identity audit path layout: {path}")
        type_mode = "on" if parts[4] == "types-on" else "off"
        range_m = parts[3].removesuffix("m")
        trial_id = (
            f"{split}/{parts[0]}/{parts[1]}/{parts[2]}/range{range_m}m/types{type_mode}/{parts[5]}"
        )
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
        indexed[str(path)] = audit_row
        count += 1
    if count:
        _write_table(manifest, list(indexed.values()))
    return count
