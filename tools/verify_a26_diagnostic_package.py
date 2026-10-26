"""Verify diagnostic hashes and relocated paths without running search."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def sha256_gzip_payload(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with gzip.open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def main() -> int:
    repo_root = Path(sys.argv[1]).resolve()
    manifest_path = repo_root / "data/accuracy/diagnostics/2026-10-26/package-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for relative, expected in manifest["files"].items():
        actual_sha, actual_bytes = sha256_file(repo_root / relative)
        if actual_sha != expected["sha256"] or actual_bytes != expected["bytes"]:
            raise SystemExit(f"File hash mismatch: {relative}")

    payload_paths = {}
    for name, item in (("catalog", manifest["catalog_payload"]), ("map", manifest["map_payload"])):
        archive = repo_root / item["archive_path"]
        raw_sha, raw_bytes = sha256_gzip_payload(archive)
        if raw_sha != item["raw_sha256"] or raw_bytes != item["raw_bytes"]:
            raise SystemExit(f"Compressed payload mismatch: {item['archive_path']}")
        payload_paths[name] = archive

    diagnostic_root = repo_root / "data/accuracy/diagnostics/2026-10-26"
    command = json.loads((diagnostic_root / "search/command-manifest.json").read_text())
    if command.get("search_truth_input") is not None:
        raise SystemExit("Search manifest contains a truth input")
    if any(
        "truth" in arg.lower() or "evaluator" in arg.lower() for arg in command["original_argv"]
    ):
        raise SystemExit("Original search argv contains evaluator paths")
    portable = command["argv"]
    required_tokens = ("{PACKAGE_ROOT}", "{CATALOG_JSON}", "{MAP_JSON}", "{OUTPUT_ROOT}")
    if not all(any(token in arg for arg in portable) for token in required_tokens):
        raise SystemExit("Portable replay argv is missing a relocation token")

    estimate = json.loads((diagnostic_root / "search/estimate.json").read_text())
    if estimate.get("search_status") != "budget_exhausted":
        raise SystemExit("Unexpected search status for the recorded diagnostic")
    truth_crs = json.loads((diagnostic_root / "evaluator/truth/truth-crs.json").read_text())
    if truth_crs.get("coordinate_reference_system") != "EPSG:32619":
        raise SystemExit("Unexpected evaluator truth CRS")

    with tempfile.TemporaryDirectory(prefix="a26-diagnostic-verify-") as temp_name:
        temp_root = Path(temp_name)
        catalog_json = temp_root / "catalog.json"
        map_json = temp_root / "map.json"
        for archive, destination in (
            (payload_paths["catalog"], catalog_json),
            (payload_paths["map"], map_json),
        ):
            with gzip.open(archive, "rb") as source, destination.open("wb") as target:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
        output_root = temp_root / "search-output"
        output_root.mkdir()
        replacements = {
            "{PACKAGE_ROOT}": str(repo_root),
            "{CATALOG_JSON}": str(catalog_json),
            "{MAP_JSON}": str(map_json),
            "{OUTPUT_ROOT}": str(output_root),
        }
        relocated_argv = [
            next(
                (
                    arg.replace(token, value)
                    for token, value in replacements.items()
                    if token in arg
                ),
                arg,
            )
            for arg in portable
        ]
        for flag in ("--measurements", "--catalog", "--map", "--config", "--scope-manifest"):
            path = Path(relocated_argv[relocated_argv.index(flag) + 1])
            if not path.is_file():
                raise SystemExit(f"Relocated input path does not exist: {flag} {path}")
        for flag in ("--output", "--telemetry-output"):
            path = Path(relocated_argv[relocated_argv.index(flag) + 1])
            if path.parent != output_root:
                raise SystemExit(f"Relocated output path is outside its temporary root: {path}")
        print(json.dumps({"relocated_replay_argv": relocated_argv}, sort_keys=True))

    print(
        json.dumps(
            {
                "status": "verified",
                "file_count": len(manifest["files"]),
                "catalog_sha256": manifest["catalog_payload"]["raw_sha256"],
                "map_sha256": manifest["map_payload"]["raw_sha256"],
                "search_status": estimate["search_status"],
                "search_invoked": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
