"""Standalone input integrity and public coast command checks."""

import csv
import hashlib
import json
import lzma
from pathlib import Path

import pytest

from maritime.coast_cli import device_ids, ensure_idle
from maritime.coast_data import prepare_inputs, sites, unpack


def sha(value):
    return hashlib.sha256(value).hexdigest()


def test_multipart_inputs_are_verified_and_reused(tmp_path):
    data, storage = tmp_path / "data", tmp_path / "storage"
    data.mkdir()
    raw = b'{"data":"independent of any older checkout"}\n'
    packed = lzma.compress(raw)
    segments = [packed[: len(packed) // 2], packed[len(packed) // 2 :]]
    spec = {
        "archive_filename": "map.json.xz",
        "archive_sha256": sha(packed),
        "raw_filename": "map.json",
        "raw_sha256": sha(raw),
        "archive_parts": [],
    }
    for index, segment in enumerate(segments):
        name = f"map.part{index}"
        (data / name).write_bytes(segment)
        spec["archive_parts"].append({"filename": name, "sha256": sha(segment)})
    (data / "catalog.json.xz").write_bytes(packed)
    catalog = {**spec, "archive_filename": "catalog.json.xz", "raw_filename": "catalog.json"}
    catalog.pop("archive_parts")
    (data / "sources.json").write_text(json.dumps({"map": spec, "catalog": catalog}))
    paths = prepare_inputs(data, storage)
    assert paths["map"].read_bytes() == raw
    modified = paths["map"].stat().st_mtime_ns
    assert prepare_inputs(data, storage) == paths
    assert paths["map"].stat().st_mtime_ns == modified
    (data / "map.part0").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="part checksum"):
        prepare_inputs(data, storage)


def test_unpack_rejects_wrong_bytes_without_leaving_output(tmp_path):
    archive, output = tmp_path / "input.xz", tmp_path / "output.json"
    packed = lzma.compress(b"real input")
    archive.write_bytes(packed)
    with pytest.raises(ValueError, match="Expanded checksum"):
        unpack(archive, output, sha(packed), sha(b"different input"))
    assert not output.exists()
    assert not list(tmp_path.glob("output.json.*"))


def test_retained_sites_are_contiguous_and_above_threshold(tmp_path):
    path = tmp_path / "observer_locations.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["site_id", "detected_charting_aids"])
        writer.writeheader()
        writer.writerows({"site_id": f"B{i}", "detected_charting_aids": 16} for i in range(1, 21))
    manifest = {"observers": {"filename": path.name, "sha256": sha(path.read_bytes())}}
    (tmp_path / "sources.json").write_text(json.dumps(manifest))
    assert len(sites(tmp_path)) == 20
    path.write_text(path.read_text().replace("B20,16", "B20,15"))
    manifest["observers"]["sha256"] = sha(path.read_bytes())
    (tmp_path / "sources.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="at least 16"):
        sites(tmp_path)


def test_device_guard_blocks_foreign_jobs_on_selected_gpus(monkeypatch):
    def command(argv, **kwargs):
        if "--query-gpu=index,uuid" in argv:
            return "0, uuid0\n1, uuid1\n2, uuid2\n"
        return "uuid1, 1234\n"

    monkeypatch.setattr("maritime.coast_cli.subprocess.check_output", command)
    ensure_idle((0, 2))
    with pytest.raises(RuntimeError, match="compute processes"):
        ensure_idle((1,))
    assert device_ids("2,3") == (2, 3)


def test_release_configuration_has_no_observer_truth():
    root = Path(__file__).resolve().parents[1]
    settings = json.loads((root / "config/search.json").read_text())
    assert set(settings) == {"map", "catalog", "search", "preparation", "marker_tolerance_deg"}
    assert settings["search"]["share_terminal_work"]
    assert settings["search"]["terminal_size_m"] == 1.0
    rows = sites(root / "data")
    assert all("legacy_site_id" not in row for row in rows)
    assert rows[13]["port"] == "Charleston"
