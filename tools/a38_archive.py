"""Crash-safe lossless JSON archive and compact-view writes for A38."""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_verified_gzip(raw: bytes, archive: Path) -> str:
    """Write a verified archive atomically; never expose a partial final path."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target:
            with gzip.GzipFile(fileobj=target, mode="wb", compresslevel=1, mtime=0) as stream:
                stream.write(raw)
            target.flush()
            os.fsync(target.fileno())
        replay = gzip.decompress(temporary.read_bytes())
        if replay != raw or sha256_bytes(replay) != sha256_bytes(raw):
            raise RuntimeError(f"Temporary gzip failed exact-byte verification: {temporary}")
        os.replace(temporary, archive)
        return sha256_bytes(archive.read_bytes())
    finally:
        temporary.unlink(missing_ok=True)


def verified_gzip_bytes(archive: Path, expected_sha256: str | None = None) -> bytes:
    """Read a complete gzip archive and optionally verify the original payload hash."""
    raw = gzip.decompress(archive.read_bytes())
    if expected_sha256 is not None and sha256_bytes(raw) != expected_sha256:
        raise RuntimeError(f"Gzip payload SHA-256 mismatch: {archive}")
    return raw


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace one file atomically after a complete write and fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
