"""Download NOAA ENC Direct aids to navigation for the Eastern Seaboard.

The service returns separate products for each ENC scale band. This script
retains the band and original S-57 layer so users can reproduce scale-aware
deduplication or catalog selection later.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv"
MANIFEST = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.manifest.json"
BBOX = (-82.0, 24.5, -66.0, 45.5)
BANDS = ("overview", "general", "coastal", "approach", "harbour", "berthing")
HOSTS = (
    "https://encdirect.noaa.gov",
    "https://gis.charttools.noaa.gov",
    "https://maritimeboundaries.noaa.gov",
)
FIELDS = (
    "OBJL",
    "BCNSHP",
    "CATLAM",
    "CATSPM",
    "COLOUR",
    "COLPAT",
    "CONDTN",
    "CONRAD",
    "CONVIS",
    "ELEVAT",
    "HEIGHT",
    "MARSYS",
    "NATCON",
    "OBJNAM",
    "VERACC",
    "VERDAT",
    "VERLEN",
    "INFORM",
    "PICREP",
    "SCAMIN",
    "SORDAT",
    "SORIND",
    "DSNM",
    "OBJECTID",
)


def read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "bearing-localization-research/1.0"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def get_service(band: str) -> tuple[str, dict[str, Any]]:
    last_error: Exception | None = None
    path = f"/arcgis/rest/services/encdirect/enc_{band}/MapServer"
    for attempt in range(10):
        for host in HOSTS:
            try:
                metadata = read_json(f"{host}{path}?f=pjson")
                if "layers" in metadata:
                    return host + path, metadata
                last_error = RuntimeError(str(metadata.get("error", metadata)))
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
        time.sleep(min(2 + attempt, 12))
    raise RuntimeError(f"Unable to load NOAA ENC {band} service: {last_error}")


def query_layer(service_path: str, layer_id: int) -> list[dict[str, Any]]:
    def retrieve_ids(spatial_filter: bool) -> tuple[list[int] | None, list[str]]:
        failures: list[str] = []
        object_ids: list[int] | None = None
        for attempt in range(2 if spatial_filter else 4):
            for host in HOSTS:
                params = {"f": "json", "where": "1=1", "returnIdsOnly": "true"}
                if spatial_filter:
                    params.update(
                        {
                            "geometry": ",".join(str(value) for value in BBOX),
                            "geometryType": "esriGeometryEnvelope",
                            "inSR": "4326",
                            "spatialRel": "esriSpatialRelIntersects",
                        }
                    )
                url = f"{host}{service_path}/{layer_id}/query?{urllib.parse.urlencode(params)}"
                try:
                    result = read_json(url)
                    if "objectIds" in result:
                        object_ids = result["objectIds"] or []
                        break
                    failures.append(f"{host}: {result.get('error', result)}")
                except (
                    OSError,
                    urllib.error.URLError,
                    TimeoutError,
                    json.JSONDecodeError,
                ) as error:
                    failures.append(f"{host}: {error}")
            if object_ids is not None:
                break
            time.sleep(1 + attempt)
        return object_ids, failures

    object_ids, failures = retrieve_ids(spatial_filter=True)
    if object_ids is None:
        object_ids, fallback_failures = retrieve_ids(spatial_filter=False)
        if object_ids is None:
            failures.extend(fallback_failures)
            raise RuntimeError(
                f"Unable to list object IDs for {service_path}/{layer_id}: " + "; ".join(failures)
            )
        print(
            f"  layer {layer_id}: spatial filter unavailable; "
            f"checking {len(object_ids):,} IDs locally",
            flush=True,
        )

    batches = [object_ids[start : start + 75] for start in range(0, len(object_ids), 75)]
    rows: list[dict[str, Any]] = []
    out_fields = "*"

    def fetch_batch(batch: list[int]) -> list[dict[str, Any]]:
        params = {
            "f": "json",
            "objectIds": ",".join(str(value) for value in batch),
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
        }
        url_query = urllib.parse.urlencode(params)
        result: dict[str, Any] | None = None
        failures = []
        for attempt in range(5):
            for host in HOSTS:
                try:
                    candidate = read_json(f"{host}{service_path}/{layer_id}/query?{url_query}")
                    if "features" in candidate:
                        result = candidate
                        break
                    failures.append(f"{host}: {candidate.get('error', candidate)}")
                except (
                    OSError,
                    urllib.error.URLError,
                    TimeoutError,
                    json.JSONDecodeError,
                ) as error:
                    failures.append(f"{host}: {error}")
            if result is not None:
                break
            time.sleep(0.5 + attempt)
        if result is None:
            raise RuntimeError(
                f"Unable to retrieve object IDs {batch[0]}–{batch[-1]} for "
                f"{service_path}/{layer_id}: " + "; ".join(failures)
            )

        return result["features"]

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(fetch_batch, batch) for batch in batches]
        for completed, future in enumerate(as_completed(futures), start=1):
            for feature in future.result():
                geometry = feature.get("geometry") or {}
                x, y = geometry.get("x"), geometry.get("y")
                if (
                    x is not None
                    and y is not None
                    and BBOX[0] <= x <= BBOX[2]
                    and BBOX[1] <= y <= BBOX[3]
                ):
                    rows.append(feature)
            if completed % 10 == 0 or completed == len(batches):
                print(
                    f"    layer {layer_id}: fetched {completed}/{len(batches)} batches",
                    flush=True,
                )
    return rows


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    band_summary: dict[str, Any] = {}
    for band in BANDS:
        service, metadata = get_service(band)
        service_path = service.removeprefix(HOSTS[0]).removeprefix(HOSTS[1]).removeprefix(HOSTS[2])
        group = next(
            (layer for layer in metadata["layers"] if layer["name"] == "AidsToNavigationP"), None
        )
        if group is None:
            raise RuntimeError(f"NOAA ENC {band} has no AidsToNavigationP layer group")
        layer_ids = set(group.get("subLayerIds") or [])
        layers = [
            layer
            for layer in metadata["layers"]
            if layer["id"] in layer_ids and layer.get("type") == "Feature Layer"
        ]
        if not layers:
            raise RuntimeError(f"NOAA ENC {band} AidsToNavigationP group has no feature layers")
        count = 0
        classes: Counter[str] = Counter()
        print(f"Downloading {band}: {len(layers)} aid classes", flush=True)
        for layer in layers:
            features = query_layer(service_path, layer["id"])
            count += len(features)
            classes[layer["name"].split(".", 1)[-1]] += len(features)
            print(f"  {layer['name'].split('.', 1)[-1]}: {len(features):,}", flush=True)
            for feature in features:
                attrs = feature.get("attributes", {})
                geometry = feature.get("geometry") or {}
                x, y = geometry.get("x"), geometry.get("y")
                if x is None or y is None:
                    continue
                all_rows.append(
                    {
                        "scale_band": band,
                        "service_layer": layer["name"],
                        "source_object_id": attrs.get("OBJECTID"),
                        "longitude": x,
                        "latitude": y,
                        **{
                            str(field).lower(): value
                            for field, value in attrs.items()
                            if field != "OBJECTID"
                        },
                    }
                )
        band_summary[band] = {
            "service_url": service,
            "feature_layers": len(layers),
            "feature_count": count,
            "counts_by_layer": dict(sorted(classes.items())),
        }
        print(f"  {count:,} feature rows", flush=True)

    all_rows.sort(
        key=lambda row: (
            row["scale_band"],
            row["service_layer"],
            int(row["source_object_id"] or 0),
        )
    )
    columns = [
        "scale_band",
        "service_layer",
        "source_object_id",
        "longitude",
        "latitude",
        *sorted(
            {
                key
                for row in all_rows
                for key in row
                if key
                not in {"scale_band", "service_layer", "source_object_id", "longitude", "latitude"}
            }
        ),
    ]
    with OUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    manifest = {
        "source": "NOAA ENC Direct to GIS",
        "source_info_url": "https://www.nauticalcharts.noaa.gov/learn/encdirect/",
        "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "region": "Eastern Seaboard bounding box, Florida Keys to Maine and adjacent waters",
        "bbox_wgs84": {
            "west": BBOX[0],
            "south": BBOX[1],
            "east": BBOX[2],
            "north": BBOX[3],
        },
        "scale_bands": band_summary,
        "feature_count_rows_including_cross_scale_repeats": len(all_rows),
        "file": OUT.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "notes": [
            "All features are point aids in the NOAA ENC AidsToNavigationP group.",
            "Cross-scale duplicate aids are retained with source scale-band metadata.",
            "Coordinates and chart attributes reflect the live NOAA ENC Direct GIS services "
            "at retrieval time.",
            "ENC Direct to GIS is a research GIS extract and is not certified for navigation.",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(all_rows):,} rows to {OUT}")
    print(f"SHA256 {digest}")


if __name__ == "__main__":
    main()
