"""Verify checksums and lossless round trips for the packaged NOAA map inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

MANIFEST = Path("data/maps/accuracy-inputs/manifest.json")
MAX_ARCHIVE_BYTES = 100_000_000


def sha256_stream(stream: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def verify_manifest(root: Path = Path(".")) -> list[str]:
    """Check every archive and every member against the indexed source checksum."""
    manifest_path = root / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    archive_dir = manifest_path.parent

    indexed_payloads = {
        manifest["collection"]["payload"]["name"]: manifest["collection"]["payload"],
        manifest["coast_mosaic"]["payload"]["name"]: manifest["coast_mosaic"]["payload"],
    }
    indexed_payloads.update({item["path"]: item for item in manifest["port_maps"]["files"]})

    for archive in manifest["archives"]:
        archive_path = archive_dir / archive["path"]
        if not archive_path.is_file():
            errors.append(f"missing archive: {archive_path}")
            continue
        if archive_path.stat().st_size >= MAX_ARCHIVE_BYTES:
            errors.append(f"archive is not below 100 MB: {archive_path}")
        if archive_path.stat().st_size != archive["bytes"]:
            errors.append(f"archive byte count mismatch: {archive_path}")
        with archive_path.open("rb") as stream:
            digest, _ = sha256_stream(stream)
        if digest != archive["sha256"]:
            errors.append(f"archive checksum mismatch: {archive_path}")

        expected = {item["name"]: item for item in archive["payloads"]}
        for name, item in expected.items():
            indexed = indexed_payloads.get(name)
            if indexed is None or (indexed["sha256"], indexed["bytes"]) != (
                item["sha256"],
                item["bytes"],
            ):
                errors.append(f"archive payload disagrees with source index: {name}")
        seen: set[str] = set()
        if archive["compression"] == "gzip":
            item = next(iter(expected.values()))
            with gzip.open(archive_path, "rb") as stream:
                digest, size = sha256_stream(stream)
            seen.add(item["name"])
            if (digest, size) != (item["sha256"], item["bytes"]):
                errors.append(f"payload mismatch: {item[name]}")
        elif archive["compression"] == "tar+gzip":
            with tarfile.open(archive_path, mode="r:gz") as tar:
                for member in tar:
                    item = expected.get(Path(member.name).name)
                    if item is None or not member.isfile():
                        errors.append(f"unexpected tar member: {member.name}")
                        continue
                    if item["name"] in seen:
                        errors.append(f"duplicate tar member: {member.name}")
                        continue
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        errors.append(f"unreadable tar member: {member.name}")
                        continue
                    digest, size = sha256_stream(extracted)
                    seen.add(item["name"])
                    if (digest, size) != (item["sha256"], item["bytes"]):
                        errors.append(f"payload mismatch: {member.name}")
        else:
            errors.append(f"unsupported compression: {archive['compression']}")
        if seen != set(expected):
            errors.append(f"archive member set mismatch: {archive_path}")

    expected_statuses = [item["coverage_status"] for item in manifest["port_maps"]["files"]]
    if len(expected_statuses) != 24:
        errors.append(f"expected 24 port maps, found {len(expected_statuses)}")
    if expected_statuses.count("known") != 19 or expected_statuses.count("partial") != 5:
        errors.append("port map coverage statuses do not match 19 known and 5 partial")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args()
    errors = verify_manifest(args.root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("Verified all map archive hashes, payload hashes, and coverage index entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
