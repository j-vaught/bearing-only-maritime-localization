from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from pyproj import Geod
from shapely.geometry import box, shape

from maritime_localization_ingest.ais import read_ais_csv
from maritime_localization_ingest.enc import (
    combine_ais_and_enc_physical_catalogs,
    compose_partial_map_v2,
    enc_aids_catalog_to_catalog_v2,
    load_enc_physical_aid_catalog,
)
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2
from maritime_localization_shared.serialization import (
    read_contract,
    write_contract,
)

ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = next(parent for parent in (ROOT, *ROOT.parents) if (parent / ".agent-work").is_dir())
CACHE = SHARED_ROOT / ".agent-work" / "cache" / "maps"
SOURCE_MAP_MANIFEST = ROOT / "data" / "maps" / "accuracy-east-coast-enc.manifest.json"
OUTPUT_MANIFEST = ROOT / "data" / "maps" / "eastern-seaboard-accuracy-v2.manifest.json"
DOMAIN_BOUNDS = (-82.0, 24.5, -66.0, 45.5)
AIS_TARGET_UTC = "2023-07-15T12:00:30Z"
GEOD = Geod(ellps="WGS84")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def geodesic_area_km2(geometry) -> float:
    area_m2, _ = GEOD.geometry_area_perimeter(geometry)
    return abs(float(area_m2)) / 1_000_000.0


def main() -> int:
    free_bytes = shutil.disk_usage(SHARED_ROOT).free
    if free_bytes < 40 * 1024**3:
        raise RuntimeError(f"Refusing new artifacts with only {free_bytes / 1024**3:.1f} GiB free.")
    CACHE.mkdir(parents=True, exist_ok=True)
    map_source_manifest = json.loads(SOURCE_MAP_MANIFEST.read_text(encoding="utf-8"))
    if map_source_manifest.get("status") != "partial":
        raise ValueError("The source ENC collection must retain its partial status.")

    input_maps: list[MapV2] = []
    map_inputs = []
    for item in map_source_manifest["port_maps"]:
        path = Path(item["map_path"])
        if not path.is_file() or sha256(path) != item["map_sha256"]:
            raise ValueError(f"Port map missing or hash mismatch: {path}")
        map_value = read_contract(path, MapV2)
        input_maps.append(map_value)
        map_inputs.append(
            {
                "site_id": item["site_id"],
                "coverage_status": item["coverage_status"],
                "path": str(path),
                "sha256": item["map_sha256"],
            }
        )

    map_version = "noaa-enc-east-coast-partial-mosaic:" + str(
        map_source_manifest.get("retrieved_at_utc", "source-epoch-unknown")
    )
    fullcoast_map = compose_partial_map_v2(
        input_maps,
        domain=box(*DOMAIN_BOUNDS),
        map_version=map_version,
    )
    map_path = CACHE / "noaa-enc-east-coast-fullcoast-map-v2.json"
    write_contract(map_path, fullcoast_map)
    if (
        fullcoast_map.coverage_geometry_geojson is None
        or fullcoast_map.unknown_geometry_geojson is None
    ):
        raise ValueError("The full-domain map must have explicit coverage and unknown geometry.")
    coverage = shape(json.loads(fullcoast_map.coverage_geometry_geojson))
    unknown = shape(json.loads(fullcoast_map.unknown_geometry_geojson))
    domain = box(*DOMAIN_BOUNDS)

    ais_path = ROOT / "data" / "ais" / "eastern-seaboard_2023-07-15T1200Z.csv"
    ais_manifest_path = ais_path.with_suffix(".manifest.json")
    ais_catalog = read_ais_csv(ais_path, target_time_utc=AIS_TARGET_UTC)
    aid_path = ROOT / "data" / "chart-aids" / "eastern-seaboard-enc-aids.csv"
    aid_manifest_path = aid_path.with_suffix(".manifest.json")
    aid_catalog = load_enc_physical_aid_catalog(aid_path)
    raw_aid_catalog = enc_aids_catalog_to_catalog_v2(aid_catalog)
    raw_aid_path = CACHE / "noaa-enc-east-coast-aids-catalog-row-identity-v2.json"
    if raw_aid_path.exists():
        saved_raw_aids = read_contract(raw_aid_path, CatalogV2)
        if saved_raw_aids != raw_aid_catalog:
            raise ValueError("Cached lossless aid catalog differs from current source conversion.")
        raw_catalog_hash = sha256(raw_aid_path)
    else:
        write_contract(raw_aid_path, raw_aid_catalog)
        raw_catalog_hash = sha256(raw_aid_path)

    combined = combine_ais_and_enc_physical_catalogs(ais_catalog, aid_catalog)
    catalog_path = CACHE / "noaa-enc-east-coast-fullcoast-catalog-v2.json"
    write_contract(catalog_path, combined)

    target = datetime.fromisoformat(AIS_TARGET_UTC.replace("Z", "+00:00"))
    ais_ages_s = [
        abs(
            (
                datetime.fromisoformat(record.report_time_utc.replace("Z", "+00:00")) - target
            ).total_seconds()
        )
        for record in ais_catalog.records
        if record.report_time_utc is not None
    ]
    motion_status = Counter(
        dict(record.metadata).get("motion_status") for record in ais_catalog.records
    )
    quality_counts = Counter(str(record.quality) for record in ais_catalog.records)
    runtime_aid_records = [
        record
        for record in combined.records
        if dict(record.metadata).get("identity_resolution") is not None
    ]
    aid_runtime_count = len(runtime_aid_records)
    aid_resolved_runtime_count = sum(
        dict(record.metadata).get("identity_ambiguous") is False for record in runtime_aid_records
    )
    aid_unresolved_reason_counts = Counter(
        dict(record.metadata).get("identity_resolution_reason", "unspecified")
        for record in runtime_aid_records
        if dict(record.metadata).get("identity_ambiguous") is True
    )
    aid_source_identity_counts = Counter(
        str(dict(record.metadata).get("source_record_identity_status", "unspecified"))
        for record in runtime_aid_records
    )
    aid_physical_linkage_counts = Counter(
        str(dict(record.metadata).get("physical_linkage_status", "unspecified"))
        for record in runtime_aid_records
    )
    aid_merged_group_count = sum(
        len(aid.feature_ids) > 1 and not aid.identity_ambiguous and bool(aid.fullname)
        for aid in aid_catalog.physical_aids
    )
    aid_fid_rows = [
        aid
        for aid in aid_catalog.features
        if any(key.endswith(".fid") and value not in (None, "") for key, value in aid.attributes)
    ]
    aid_unnamed_fid_rows = [
        aid
        for aid in aid_fid_rows
        if not any(
            key in {"fullname", "objnam", "name"} and value not in (None, "")
            for key, value in aid.attributes
        )
    ]
    type_counts = Counter(record.normalized_type or "untyped" for record in combined.records)
    port_status_counts = Counter(item["coverage_status"] for item in map_inputs)
    map_manifest = {
        "schema_version": 1,
        "domain_name": map_source_manifest["domain_name"],
        "domain_bbox_wgs84": list(DOMAIN_BOUNDS),
        "coverage_status": fullcoast_map.coverage_status,
        "unknown_policy": (
            "Only source-reported MapV2 coverage is eligible; every domain point outside "
            "the surviving union, including ENC seams and out-of-port coast, is explicit unknown."
        ),
        "known_coverage_area_km2_geodesic": geodesic_area_km2(coverage),
        "explicit_unknown_area_km2_geodesic": geodesic_area_km2(unknown),
        "domain_area_km2_geodesic": geodesic_area_km2(domain),
        "known_coverage_fraction": coverage.area / domain.area,
        "source_manifest": {
            "path": str(SOURCE_MAP_MANIFEST),
            "sha256": sha256(SOURCE_MAP_MANIFEST),
            "source_collection_path": map_source_manifest["collection_path"],
            "source_collection_sha256": map_source_manifest["collection_sha256"],
            "source_collection_status": map_source_manifest["status"],
            "source_feature_count": map_source_manifest["feature_count"],
            "source_issue_count": len(map_source_manifest["issues"]),
            "port_map_count": len(map_inputs),
            "port_coverage_status_counts": dict(sorted(port_status_counts.items())),
            "ports": map_inputs,
        },
        "output": {
            "path": str(map_path),
            "sha256": sha256(map_path),
            "bytes": map_path.stat().st_size,
            "map_version": fullcoast_map.map_version,
            "crs": fullcoast_map.crs,
        },
        "limitations": [
            "The source ENC collection is partial and contains 314 acquisition issues.",
            "The 24 port map domains do not form complete coast coverage.",
            "Unknown takes precedence where a source map reports unknown overlap.",
            (
                "The five partial port maps contribute only validated reported coverage; "
                "their no-data seams remain unknown."
            ),
            (
                "This MapV2 is an Eastern Seaboard coverage mosaic, not a complete navigational "
                "chart or a claim of complete coastline coverage."
            ),
        ],
    }

    output_manifest = {
        "schema_version": 1,
        "catalog": {
            "path": str(catalog_path),
            "sha256": sha256(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "catalog_id": combined.catalog_id,
            "catalog_version": combined.catalog_version,
            "catalog_timestamp_utc": combined.timestamp_utc,
            "record_count": len(combined.records),
            "record_count_by_normalized_type": dict(sorted(type_counts.items())),
            "ais": {
                "source_path": str(ais_path),
                "source_sha256": sha256(ais_path),
                "source_manifest_path": str(ais_manifest_path),
                "source_manifest_sha256": sha256(ais_manifest_path),
                "source_snapshot_window_utc": [
                    json.loads(ais_manifest_path.read_text(encoding="utf-8"))["time_start_utc"],
                    json.loads(ais_manifest_path.read_text(encoding="utf-8"))[
                        "time_end_utc_exclusive"
                    ],
                ],
                "reference_timestamp_utc": ais_catalog.timestamp_utc,
                "record_count": len(ais_catalog.records),
                "mmsi_present_in_source_export": False,
                "motion_status_counts": dict(sorted(motion_status.items())),
                "quality_counts": dict(sorted(quality_counts.items())),
                "absolute_report_age_seconds": {
                    "minimum": min(ais_ages_s),
                    "median": sorted(ais_ages_s)[len(ais_ages_s) // 2],
                    "maximum": max(ais_ages_s),
                },
                "source_key_identity_preserved": True,
                "stale_reports_are_not_retimed_or_extrapolated": True,
            },
            "physical_aids": {
                "source_path": str(aid_path),
                "source_sha256": sha256(aid_path),
                "source_manifest_path": str(aid_manifest_path),
                "source_manifest_sha256": sha256(aid_manifest_path),
                "source_retrieved_utc": aid_catalog.retrieved_utc,
                "source_raw_component_count": len(aid_catalog.features),
                "qualified_fid_source_row_count": len(aid_fid_rows),
                "unnamed_qualified_fid_source_row_count": len(aid_unnamed_fid_rows),
                "qualified_fid_source_keys_are_unique": len(
                    {aid.feature_id for aid in aid_fid_rows}
                )
                == len(aid_fid_rows),
                "lossless_raw_catalog_path": str(raw_aid_path),
                "lossless_raw_catalog_sha256": raw_catalog_hash,
                "lossless_raw_catalog_record_count": len(raw_aid_catalog.records),
                "physical_group_count": len(aid_catalog.physical_aids),
                "runtime_physical_record_count": aid_runtime_count,
                "runtime_resolved_physical_entity_count": aid_resolved_runtime_count,
                "runtime_unresolved_component_count": (
                    aid_runtime_count - aid_resolved_runtime_count
                ),
                "runtime_unresolved_reason_counts": dict(
                    sorted(aid_unresolved_reason_counts.items())
                ),
                "runtime_source_record_identity_status_counts": dict(
                    sorted(aid_source_identity_counts.items())
                ),
                "runtime_physical_linkage_status_counts": dict(
                    sorted(aid_physical_linkage_counts.items())
                ),
                "runtime_unresolved_records_remain_geometry_candidates": True,
                "observation_association_is_not_inferred_from_physical_linkage": True,
                "support_counts_only_resolved_representative_after_angular_merge": True,
                "physical_linkage_heuristic": (
                    "same-chart normalized-name complementary-component grouping within 20 m; "
                    "heuristic and not externally validated"
                ),
                "raw_component_count_preserved_in_runtime_or_resolved_provenance": (
                    len(aid_catalog.features)
                ),
                "merged_component_groups": aid_merged_group_count,
                "unresolved_components_are_retained_for_search": True,
                "unresolved_components_are_not_proven_distinct_support": True,
                "source_epochs_retained_per_component": True,
                "raw_feature_ids_and_attributes_retained": True,
            },
        },
        "map": map_manifest,
        "time_and_support_policy": {
            "catalog_time_is_the_ais_source_reference_instant": True,
            "enc_aids_retain_source_retrieval_and_chart_epoch_per_provenance": True,
            "ais_and_enc_are_mixed_epoch_inputs": True,
            "unknown_map_regions_are_not_inferred_as_water_or_land": True,
            "range_los_and_angular_merge_must_precede_support_counts": True,
        },
        "artifact_paths_are_server_local": True,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(output_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Catalog: {catalog_path} sha256={sha256(catalog_path)} records={len(combined.records)}")
    print(
        f"Raw aid components: {raw_aid_path} sha256={raw_catalog_hash} "
        f"records={len(raw_aid_catalog.records)}"
    )
    print(f"Map: {map_path} sha256={sha256(map_path)} status={fullcoast_map.coverage_status}")
    print(f"Manifest: {OUTPUT_MANIFEST} sha256={sha256(OUTPUT_MANIFEST)}")
    print(
        f"Physical aids runtime={aid_runtime_count}; "
        f"groups={len(aid_catalog.physical_aids)}; raw rows={len(aid_catalog.features)}"
    )
    print(
        f"AIS records={len(ais_catalog.records)}; report age seconds "
        f"min/median/max={min(ais_ages_s)}/"
        f"{sorted(ais_ages_s)[len(ais_ages_s) // 2]}/{max(ais_ages_s)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
