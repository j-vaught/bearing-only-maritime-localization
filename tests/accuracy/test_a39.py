import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from runpy import run_path
from typing import Any

VERIFY_MANIFEST = run_path(
    str(Path(__file__).resolve().parents[2] / "tools/package_accuracy_map_inputs.py"),
    run_name="a39_test",
)["verify_manifest"]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_gzip(path: Path, value: bytes) -> None:
    with gzip.open(path, "wb") as stream:
        stream.write(value)


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _record(name: str, payload: bytes, **extra: Any) -> dict[str, Any]:
    return {"name": name, "bytes": len(payload), "sha256": _sha256(payload), **extra}


def _fixture_root(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path
    archive_dir = root / "data/maps/accuracy-inputs"
    archive_dir.mkdir(parents=True)
    collection_name = "collection.geojson"
    mosaic_name = "mosaic.json"
    collection = b'{"features":[]}'
    mosaic = b'{"coverage":"partial"}'
    ports = {f"map-{index:02d}.json": f'{{"port":{index}}}'.encode() for index in range(1, 25)}
    _write_gzip(archive_dir / "collection.geojson.gz", collection)
    _write_gzip(archive_dir / "mosaic.json.gz", mosaic)
    _write_tar(archive_dir / "ports.tar.gz", ports)

    port_records = [
        {
            "path": name,
            "bytes": len(payload),
            "sha256": _sha256(payload),
            "coverage_status": "known" if index <= 19 else "partial",
        }
        for index, (name, payload) in enumerate(ports.items(), start=1)
    ]
    archives = []
    for archive_name, compression, payloads in (
        ("collection.geojson.gz", "gzip", [(collection_name, collection)]),
        ("mosaic.json.gz", "gzip", [(mosaic_name, mosaic)]),
        ("ports.tar.gz", "tar+gzip", list(ports.items())),
    ):
        archive_path = archive_dir / archive_name
        archive_records = [_record(name, payload) for name, payload in payloads]
        archives.append(
            {
                "path": archive_name,
                "compression": compression,
                "bytes": archive_path.stat().st_size,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "payloads": archive_records,
            }
        )
    manifest = {
        "collection": {"payload": _record(collection_name, collection)},
        "coast_mosaic": {"payload": _record(mosaic_name, mosaic)},
        "port_maps": {"files": port_records},
        "archives": archives,
    }
    (archive_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest


def _save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = root / "data/maps/accuracy-inputs/manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _archive(root: Path, name: str) -> Path:
    return root / "data/maps/accuracy-inputs" / name


def _refresh_archive_metadata(root: Path, manifest: dict[str, Any], name: str) -> None:
    path = _archive(root, name)
    entry = next(item for item in manifest["archives"] if item["path"] == name)
    entry["bytes"] = path.stat().st_size
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_accuracy_map_archives_round_trip_and_match_index() -> None:
    root = Path(__file__).resolve().parents[2]
    assert VERIFY_MANIFEST(root) == []


def test_archive_byte_count_mismatch_is_reported(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    manifest["archives"][0]["bytes"] += 1
    _save_manifest(root, manifest)
    assert any("archive byte count mismatch" in error for error in VERIFY_MANIFEST(root))


def test_archive_hash_mismatch_is_reported(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    manifest["archives"][0]["sha256"] = "0" * 64
    _save_manifest(root, manifest)
    assert any("archive checksum mismatch" in error for error in VERIFY_MANIFEST(root))


def test_gzip_payload_mismatch_is_reported_without_exception(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    manifest["archives"][0]["payloads"][0]["sha256"] = "0" * 64
    _save_manifest(root, manifest)
    errors = VERIFY_MANIFEST(root)
    assert any("payload mismatch: collection.geojson" in error for error in errors)


def test_missing_tar_member_is_reported(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    _write_tar(_archive(root, "ports.tar.gz"), {"map-01.json": b'{"port":1}'})
    _refresh_archive_metadata(root, manifest, "ports.tar.gz")
    _save_manifest(root, manifest)
    assert any(
        "archive member set mismatch: ports.tar.gz" in error for error in VERIFY_MANIFEST(root)
    )


def test_unexpected_tar_member_is_reported(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    members = {
        item["path"]: f'{{"port":{index}}}'.encode()
        for index, item in enumerate(manifest["port_maps"]["files"], 1)
    }
    members["unexpected.json"] = b"{}"
    _write_tar(_archive(root, "ports.tar.gz"), members)
    _refresh_archive_metadata(root, manifest, "ports.tar.gz")
    _save_manifest(root, manifest)
    assert any("unexpected tar member: unexpected.json" in error for error in VERIFY_MANIFEST(root))


def test_truncated_gzip_returns_readable_failure(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    path = _archive(root, "collection.geojson.gz")
    path.write_bytes(b"\x1f\x8b\x08")
    _refresh_archive_metadata(root, manifest, path.name)
    _save_manifest(root, manifest)
    errors = VERIFY_MANIFEST(root)
    assert any(
        "archive is corrupt or truncated: collection.geojson.gz" in error for error in errors
    )


def test_corrupt_tar_archive_returns_readable_failure(tmp_path: Path) -> None:
    root, manifest = _fixture_root(tmp_path)
    path = _archive(root, "ports.tar.gz")
    path.write_bytes(b"not a gzip or tar archive")
    _refresh_archive_metadata(root, manifest, path.name)
    _save_manifest(root, manifest)
    errors = VERIFY_MANIFEST(root)
    assert any("archive is corrupt or truncated: ports.tar.gz" in error for error in errors)
