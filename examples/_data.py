"""Verify and unpack the current catalog and map for manual search."""

from __future__ import annotations

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


def prepare_data() -> tuple[Path, Path]:
    """Unpack one shared copy beside the committed compressed inputs."""
    catalog_folder = ROOT / "data"
    catalog = json.loads((catalog_folder / "objects.manifest.json").read_text())
    objects_path = catalog_folder / "objects.json"
    map_path = catalog_folder / "map.json"
    unpack(
        ROOT / catalog["archive_path"],
        objects_path,
        catalog["archive_sha256"],
        catalog["raw_sha256"],
    )
    map_payload = json.loads((catalog_folder / "map.manifest.json").read_text())["map_payload"]
    unpack(
        catalog_folder / map_payload["archive_filename"],
        map_path,
        map_payload["archive_sha256"],
        map_payload["original_sha256"],
    )
    return objects_path, map_path
