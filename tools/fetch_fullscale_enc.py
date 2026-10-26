from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import os
import random
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = (-82.0, 24.5, -66.0, 45.5)
BANDS = {
    "berthing": ("enc_berthing", "Berthing"),
    "harbour": ("enc_harbour", "Harbor"),
    "approach": ("enc_approach", "Approach"),
}
BASE_URL = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"
OUT = ROOT / ".agent-work" / "cache" / "maps" / "east-coast-fullscale"
OUT.mkdir(parents=True, exist_ok=True)


def request_json(url: str, params: dict[str, Any], *, attempts: int = 6) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                f"{url}?{query}", headers={"User-Agent": "bearing-only-maritime-localization/1.0"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            if isinstance(payload.get("error"), dict):
                raise RuntimeError(str(payload["error"]))
            return payload
        except Exception as error:  # NOAA services intermittently return transient 500s.
            last_error = error
            time.sleep(min(0.5 * (2**attempt), 12.0) + random.random() * 0.25)
    raise RuntimeError(
        f"NOAA request failed after {attempts} attempts: {url} {params}"
    ) from last_error


def service_layers(service: str) -> tuple[int, int]:
    metadata = request_json(f"{BASE_URL}/{service}/MapServer", {"f": "pjson"})
    names = {layer["name"].rsplit(".", 1)[-1]: int(layer["id"]) for layer in metadata["layers"]}
    return names["Coverage_area"], names["Land_Area"]


def tile_boxes() -> list[tuple[float, float, float, float]]:
    west, south, east, north = DOMAIN
    boxes: list[tuple[float, float, float, float]] = []
    x = west
    while x < east:
        y = south
        while y < north:
            boxes.append((x, y, min(x + 1.0, east), min(y + 1.0, north)))
            y += 1.0
        x += 1.0
    return boxes


def query_ids(url: str, bbox: tuple[float, float, float, float], *, attempts: int = 6) -> list[int]:
    west, south, east, north = bbox
    payload = request_json(
        url,
        {
            "f": "json",
            "where": "1=1",
            "geometry": f"{west},{south},{east},{north}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "returnIdsOnly": "true",
        },
        attempts=attempts,
    )
    return [int(value) for value in (payload.get("objectIds") or [])]


def download_tiled_ids(url: str) -> list[int]:
    try:
        print("  trying one full-domain ID query", flush=True)
        return query_ids(url, DOMAIN, attempts=2)
    except RuntimeError as error:
        print(f"  using tiled ID retrieval after full-domain query failed: {error}", flush=True)
    west, south, east, north = DOMAIN
    regional = [
        (west, south, (west + east) / 2, (south + north) / 2),
        ((west + east) / 2, south, east, (south + north) / 2),
        (west, (south + north) / 2, (west + east) / 2, north),
        ((west + east) / 2, (south + north) / 2, east, north),
    ]
    try:
        regional_ids: set[int] = set()
        for bbox in regional:
            regional_ids.update(query_ids(url, bbox, attempts=3))
        print(f"  regional ID queries complete; unique IDs {len(regional_ids)}", flush=True)
        return sorted(regional_ids)
    except RuntimeError:
        print("  using 1-degree tiles after regional query errors", flush=True)
    tiles = tile_boxes()
    result: set[int] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(query_ids, url, bbox) for bbox in tiles]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result.update(future.result())
            if index % 50 == 0:
                print(
                    f"  spatial tiles complete {index}/{len(tiles)}; unique IDs {len(result)}",
                    flush=True,
                )
    return sorted(result)


def fetch_by_ids(url: str, ids: list[int], *, chunk_size: int = 100) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    returned: set[int] = set()
    for start in range(0, len(ids), chunk_size):
        group = ids[start : start + chunk_size]
        payload = request_json(
            url,
            {
                "f": "geojson",
                "objectIds": ",".join(map(str, group)),
                "outFields": "*",
                "outSR": 4326,
                "returnGeometry": "true",
            },
        )
        batch = payload.get("features", [])
        for feature in batch:
            props = feature.get("properties") or {}
            oid = props.get("OBJECTID", feature.get("id"))
            if oid is None:
                raise ValueError("NOAA feature omitted its object ID.")
            returned.add(int(oid))
            if not feature.get("geometry"):
                raise ValueError(f"NOAA feature {oid} omitted geometry.")
        features.extend(batch)
        if start and start % 2000 == 0:
            print(f"  feature batches {min(start, len(ids))}/{len(ids)}", flush=True)
    if returned != set(ids):
        missing = sorted(set(ids) - returned)
        raise ValueError(f"NOAA feature retrieval was incomplete; missing IDs {missing[:20]}.")
    return features


def query_land_ids(url: str, cell_ids: list[str]) -> list[int]:
    quoted = ",".join("'" + item.replace("'", "''") + "'" for item in cell_ids)
    try:
        payload = request_json(
            url, {"f": "json", "where": f"DSNM IN ({quoted})", "returnIdsOnly": "true"}
        )
        return [int(value) for value in (payload.get("objectIds") or [])]
    except RuntimeError:
        if len(cell_ids) <= 1:
            raise
        middle = len(cell_ids) // 2
        return query_land_ids(url, cell_ids[:middle]) + query_land_ids(url, cell_ids[middle:])


def get_band(band: str, service: str, layer_prefix: str) -> dict[str, Any]:
    cached = OUT / f"{band}.geojson.gz"
    manifest_path = OUT / f"{band}.manifest.json"
    if cached.is_file() and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest.get("complete")
            and manifest.get("sha256") == hashlib.sha256(cached.read_bytes()).hexdigest()
        ):
            print(
                f"{band}: using verified cache with {manifest['feature_count']} features",
                flush=True,
            )
            return manifest

    coverage_layer, land_layer = service_layers(service)
    base = f"{BASE_URL}/{service}/MapServer"
    print(f"{band}: finding coverage cells across the full coast", flush=True)
    coverage_ids = download_tiled_ids(f"{base}/{coverage_layer}/query")
    print(f"{band}: fetching {len(coverage_ids)} coverage features", flush=True)
    coverage_features = fetch_by_ids(f"{base}/{coverage_layer}/query", coverage_ids)
    cell_ids = sorted(
        {
            str((feature.get("properties") or {}).get("DSNM"))
            for feature in coverage_features
            if (feature.get("properties") or {}).get("DSNM")
        }
    )
    print(f"{band}: querying land features for {len(cell_ids)} chart cells", flush=True)
    chunks = [cell_ids[index : index + 20] for index in range(0, len(cell_ids), 20)]
    land_ids: set[int] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(query_land_ids, f"{base}/{land_layer}/query", chunk) for chunk in chunks
        ]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            land_ids.update(future.result())
            if index % 20 == 0:
                print(
                    f"  chart groups complete {index}/{len(chunks)}; land IDs {len(land_ids)}",
                    flush=True,
                )
    print(f"{band}: fetching {len(land_ids)} land features", flush=True)
    land_features = fetch_by_ids(f"{base}/{land_layer}/query", sorted(land_ids))
    features = []
    for layer_name, batch in (("Coverage_area", coverage_features), ("Land_Area", land_features)):
        for feature in batch:
            props = dict(feature.get("properties") or {})
            props["scale_band"] = band
            props["service_layer"] = f"{layer_prefix}.{layer_name}"
            props["source"] = "NOAA ENC Direct"
            feature["properties"] = props
            features.append(feature)
    document = {
        "type": "FeatureCollection",
        "name": f"NOAA ENC Direct full-coast {band}",
        "features": features,
    }
    payload = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    archive = gzip.compress(payload, compresslevel=9, mtime=0)
    cached.write_bytes(archive)
    manifest = {
        "complete": True,
        "service": f"{BASE_URL}/{service}/MapServer",
        "scale_band": band,
        "coverage_layer_id": coverage_layer,
        "land_layer_id": land_layer,
        "coverage_feature_count": len(coverage_features),
        "coverage_object_id_count": len(coverage_ids),
        "land_feature_count": len(land_features),
        "land_object_id_count": len(land_ids),
        "chart_cell_count": len(cell_ids),
        "feature_count": len(features),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "retrieved_utc": datetime.now(UTC).isoformat(),
        "archive_path": str(cached),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{band}: wrote {len(features)} features, {len(archive) / 1e6:.1f} MB", flush=True)
    return manifest


def main() -> None:
    summaries = []
    selected = os.environ.get("ENC_BANDS", "berthing,harbour,approach").split(",")
    for band in selected:
        service, prefix = BANDS[band]
        summaries.append(get_band(band, service, prefix))
    print(json.dumps(summaries, indent=2), flush=True)


if __name__ == "__main__":
    main()
