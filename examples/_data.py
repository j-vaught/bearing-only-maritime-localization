"""Load checksum-verified AIS, chart aids, or both with one shared map."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CatalogMode = Literal["ais", "chart_aids", "both"]


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


def catalog_spec(mode: CatalogMode) -> dict[str, Any]:
    """Read the checked-in definition of a catalog selection."""
    sources = json.loads((DATA / "sources.json").read_text())
    if mode == "both":
        return sources["combined"]
    if mode not in ("ais", "chart_aids"):
        raise ValueError("catalog_mode must be 'ais', 'chart_aids', or 'both'.")
    return sources["catalogs"][mode]


def _combine(sources: dict[str, Any], output: Path) -> None:
    """Create an ignored combined file in deterministic original record order."""
    spec = sources["combined"]
    if output.exists():
        if digest(output) != spec["raw_sha256"]:
            raise FileExistsError(f"Different generated catalog already exists: {output}")
        return
    payload = dict(spec["header"])
    records = []
    for mode in spec["merge_order"]:
        part = sources["catalogs"][mode]
        records.extend(json.loads((DATA / part["raw_filename"]).read_text())["records"])
    if len(records) != spec["record_count"] or len({r["key"] for r in records}) != len(records):
        raise ValueError("Catalog combination changed record count or introduced duplicate keys.")
    payload["records"] = records
    descriptor, name = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.write("\n")
        if digest(temporary) != spec["raw_sha256"]:
            raise ValueError("Combined catalog checksum differs from sources.json.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_data(mode: CatalogMode = "both") -> tuple[Path, Path]:
    """Unpack selected sources. Only compressed independent catalogs are committed."""
    sources = json.loads((DATA / "sources.json").read_text())
    spec = catalog_spec(mode)
    selected = sources["combined"]["merge_order"] if mode == "both" else (mode,)
    for selection in selected:
        part = sources["catalogs"][selection]
        unpack(
            DATA / part["archive_filename"],
            DATA / part["raw_filename"],
            part["archive_sha256"],
            part["raw_sha256"],
        )
    objects_path = DATA / spec["raw_filename"]
    if mode == "both":
        _combine(sources, objects_path)
    map_payload = sources["map"]
    map_path = DATA / map_payload["raw_filename"]
    unpack(
        DATA / map_payload["archive_filename"],
        map_path,
        map_payload["archive_sha256"],
        map_payload["raw_sha256"],
    )
    return objects_path, map_path
