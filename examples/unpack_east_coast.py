"""Verify and unpack the current catalog and map for manual search."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def unpack(archive: Path, output: Path, archive_hash: str, raw_hash: str) -> None:
    if digest(archive) != archive_hash:
        raise ValueError(f"Archive checksum mismatch: {archive}")
    if output.exists():
        if digest(output) != raw_hash:
            raise FileExistsError(f"Different file already exists: {output}")
        print(f"Already verified {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target, gzip.open(archive, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
        if digest(temporary) != raw_hash:
            raise ValueError(f"Uncompressed checksum mismatch: {archive}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Verified {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/east-coast")
    args = parser.parse_args()
    catalog_folder = ROOT / "data/catalogs/accuracy-inputs-v2"
    catalog = json.loads((catalog_folder / "manifest.json").read_text())
    unpack(
        ROOT / catalog["archive_path"],
        args.output_dir / "catalog.json",
        catalog["archive_sha256"],
        catalog["raw_sha256"],
    )
    map_folder = ROOT / "data/maps/accuracy-inputs-v3"
    map_payload = json.loads((map_folder / "manifest.json").read_text())["map_payload"]
    unpack(
        map_folder / map_payload["archive_filename"],
        args.output_dir / "map.json",
        map_payload["archive_sha256"],
        map_payload["original_sha256"],
    )


if __name__ == "__main__":
    main()
