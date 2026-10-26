from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "acquire_accuracy_coast_base", ROOT / "tools/acquire_accuracy_coast_base.py"
)
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load the acquisition module")
coast = cast(Any, importlib.util.module_from_spec(spec))
spec.loader.exec_module(coast)


class SplitClient:
    def __init__(self, missing: set[int] | None = None):
        self.missing = missing or set()
        self.requests = 0
        self.errors: list[dict[str, object]] = []

    def get_first(self, path, params):
        return {"objectIds": [1, 2, 3, 4, 5]}, "https://source/id-query", "id-hash"

    def query_batch(self, path, layer_id, ids, id_field):
        self.requests += 1
        if len(ids) > 2 or any(value in self.missing for value in ids):
            return None, {"requested_ids": ids, "failures": [{"error": "mock split"}]}
        rows = [
            {
                "attributes": {id_field: value, "CATCOV": 1},
                "geometry": {"rings": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
            }
            for value in ids
        ]
        return rows, {"requested_ids": ids, "returned_ids": ids, "response_sha256": "mock"}


def test_json_envelope_uses_arcgis_esri_geometry_json() -> None:
    value = json.loads(coast.envelope((-82.0, 24.5, -66.0, 45.5)))
    assert value == {
        "xmin": -82.0,
        "ymin": 24.5,
        "xmax": -66.0,
        "ymax": 45.5,
        "spatialReference": {"wkid": 4326},
    }


def test_adaptive_split_recovers_exact_ids() -> None:
    client = SplitClient()
    result = coast.acquire_layer(
        client,
        band="general",
        role="land_area",
        path="/arcgis/rest/services/encdirect/enc_general/MapServer",
        layer={"id": 121, "name": "enc_general.land_area", "objectIdField": "OBJECTID"},
        bbox=coast.BBOX,
        started_utc="2026-01-01T00:00:00Z",
    )
    assert result["exact_id_complete"]
    assert result["feature_count"] == 5
    assert result["missing_ids"] == []
    assert client.requests > 1


def test_missing_feature_survives_as_explicit_incompleteness() -> None:
    client = SplitClient({4})
    result = coast.acquire_layer(
        client,
        band="general",
        role="land_area",
        path="/arcgis/rest/services/encdirect/enc_general/MapServer",
        layer={"id": 121, "name": "enc_general.land_area", "objectIdField": "OBJECTID"},
        bbox=coast.BBOX,
        started_utc="2026-01-01T00:00:00Z",
    )
    assert result["missing_ids"] == [4]
    assert not result["exact_id_complete"]
    assert result["archive"]["requested_ids"] == [1, 2, 3, 4, 5]
    assert result["archive"]["returned_ids"] == [1, 2, 3, 5]


def test_duplicate_or_gapped_response_is_rejected() -> None:
    for returned in (
        [{"attributes": {"OBJECTID": 1}}, {"attributes": {"OBJECTID": 1}}],
        [{"attributes": {"OBJECTID": 1}}],
    ):
        client = coast.BoundedClient()
        client.request = lambda _url, rows=returned: ({"features": rows}, b'{"features":[]}', None)
        features, record = client.query_batch("/service", 7, [1, 2], "OBJECTID")
        assert features is None
        assert record["failures"][0]["error"] == "source ID set mismatch"


def test_published_archive_hashes_and_feature_counts_match_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "data/maps/coast-base/manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    for key, item in manifest["source_layers"].items():
        if not item.get("archive_path"):
            assert not item.get("complete")
            continue
        archive_path = root / item["archive_path"]
        if not archive_path.exists():
            assert not item.get("complete")
            continue
        compressed = archive_path.read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == item["archive_sha256"], key
        archive = json.loads(gzip.decompress(compressed))
        assert len(archive["features"]) == item["feature_count"], key
        assert archive["requested_ids"] == sorted(set(archive["requested_ids"])), key
        assert len(archive["invalid_geometry_ids"]) == len(item["invalid_geometry_ids"]), key


def test_adaptive_spatial_id_fallback_unions_tile_ids() -> None:
    from urllib.parse import parse_qs, urlsplit

    class TileClient:
        def __init__(self):
            self.errors = []

        def get_first(self, path, params):
            return None, None, None

        def request(self, url):
            query = parse_qs(urlsplit(url).query)
            geometry = json.loads(query["geometry"][0])
            if geometry["xmax"] - geometry["xmin"] > 8.01:
                return None, b"", "mock full extent failure"
            return {"objectIds": [int(geometry["xmin"] < -74) + 1]}, b"tile", None

    ids, record, complete = coast.query_ids_tiled(
        TileClient(), "/service", 9, (-82.0, 24.5, -66.0, 45.5), max_depth=2
    )
    assert complete
    assert ids == [1, 2]
    assert record["mode"] == "adaptive_spatial_tiles"
    assert len(record["tiles"]) == 4


def test_mapv2_candidate_is_hashed_partial_and_internally_consistent() -> None:
    from shapely.geometry import shape

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "data/maps/coast-base/manifest.json").read_text())
    item = manifest.get("base_map_v2", {})
    if not item.get("path"):
        return
    assert item["complete"] is True
    archive = (root / item["path"]).read_bytes()
    assert hashlib.sha256(archive).hexdigest() == item["sha256"]
    from maritime_localization_shared.contracts.inputs_v2 import MapV2

    parsed = MapV2(**json.loads(gzip.decompress(archive)))
    assert parsed.coverage_status == "partial"
    assert parsed.crs == "EPSG:4326"
    assert parsed.coverage_geometry_geojson is not None
    assert parsed.unknown_geometry_geojson is not None
    assert parsed.land_geometry_geojson is not None
    coverage = shape(json.loads(parsed.coverage_geometry_geojson))
    unknown = shape(json.loads(parsed.unknown_geometry_geojson))
    land = shape(json.loads(parsed.land_geometry_geojson))
    assert coverage.intersection(unknown).area < 1e-8
    assert land.difference(coverage).area < 1e-8
    assert item["complete_scale_bands"] == ["coastal", "general"]
