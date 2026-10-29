"""Post hoc aid report geometry from saved suite observations. Author J.C. Vaught."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path
from statistics import median

from pyproj import Geod

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "b-location-suite/results-20261029/results.json"
GEOD = Geod(ellps="WGS84")
REMOTE_REPO = "/home/jvaught2/projects/bearing-only-maritime-localization"

REMOTE_EXTRACT = r"""
import base64, hashlib, json, math, pathlib, sys
from dataclasses import asdict
from pyproj import Geod
from maritime.catalog_identity import canonicalize_chart_aids
from maritime.schemas.inputs_v2 import CatalogV2
from maritime.serialization import read_contract
repo = pathlib.Path.cwd()
rows = json.loads(base64.b64decode(sys.argv[1]))
geod = Geod(ellps="WGS84")
catalogs = {}
output = {"author": "J.C. Vaught", "sites": [], "catalogs": {}, "source_code_sha256": {}}
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
for name in ["maritime/catalog_identity.py", "maritime/simulator/runner.py", "maritime/geometry/angular_grouping.py"]:
    output["source_code_sha256"][name] = sha(repo / name)
for row in rows:
    attempt = pathlib.Path(row["attempt_directory"])
    files = {name: json.loads((attempt / name).read_text()) for name in ["measurements.json", "simulation-diagnostics.json", "truth.json"]}
    settings_path = attempt / "settings.json"
    if settings_path.exists():
        files["settings.json"] = json.loads(settings_path.read_text())
        settings_source = str(settings_path)
    else:
        manifest_path = attempt.parents[1] / "manifest.json"
        files["settings.json"] = json.loads(manifest_path.read_text())["provenance"]["settings"]
        settings_source = str(manifest_path)
    catalog_path = pathlib.Path(files["settings.json"]["catalog"])
    if not catalog_path.is_absolute():
        catalog_path = repo / catalog_path
    catalog_name = str(catalog_path)
    if catalog_name not in catalogs:
        raw = read_contract(catalog_path, CatalogV2)
        canonical = canonicalize_chart_aids(raw)
        catalogs[catalog_name] = {r.key: r for r in canonical.records}
        output["catalogs"][catalog_name] = {"sha256": sha(catalog_path), "raw_record_count": len(raw.records), "canonical_record_count": len(canonical.records)}
    by_key = catalogs[catalog_name]
    keys = {key for g in files["simulation-diagnostics.json"]["groups"] if g["detected"] for key in g["catalog_member_keys"]}
    records = {}
    for key in sorted(keys):
        r = by_key[key]
        metadata = dict(r.metadata)
        east = float(metadata.get("visible_feature_offset_east_m", 0.0))
        north = float(metadata.get("visible_feature_offset_north_m", 0.0))
        lon, lat = r.longitude_deg, r.latitude_deg
        if east or north:
            lon, lat, _ = geod.fwd(lon, lat, math.degrees(math.atan2(east, north)), math.hypot(east, north))
        records[key] = {"key": key, "latitude_deg": lat, "longitude_deg": lon, "catalog_latitude_deg": r.latitude_deg, "catalog_longitude_deg": r.longitude_deg, "normalized_type": r.normalized_type, "physical_entity_id": metadata.get("physical_entity_id"), "visible_feature_offset_east_m": east, "visible_feature_offset_north_m": north}
    output["sites"].append({"site_id": row["site_id"], "attempt_directory": str(attempt), "settings_source": settings_source, "source_sha256": {name: sha(attempt / name) for name in files if (attempt / name).exists()}, "saved_inputs": files, "joined_catalog_records": records})
print(json.dumps(output))
"""


def refresh_evidence(rows):
    """Read saved artifacts and canonicalize existing identities without simulation."""
    payload = base64.b64encode(
        json.dumps(
            [{k: row[k] for k in ("site_id", "attempt_directory")} for row in rows]
        ).encode()
    ).decode()
    result = subprocess.run(
        ["ssh", "comech-2080", f"cd {REMOTE_REPO} && .venv/bin/python - {payload}"],
        input=REMOTE_EXTRACT,
        text=True,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    evidence = json.loads(result.stdout)
    (HERE / "aid-clustering-evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    return evidence


def quantile(values, probability):
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lo = math.floor(index)
    hi = math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def angular_metrics(bearings):
    """Circular distances exclude self and preserve real coincident observations."""
    if len(bearings) < 2:
        return {
            "occupied_span_deg": 0.0 if bearings else None,
            "median_nearest_neighbor_deg": None,
        }
    ordered = sorted(b % 360 for b in bearings)
    gaps = [(ordered[(i + 1) % len(ordered)] - b) % 360 for i, b in enumerate(ordered)]
    # The closing gap for identical endpoints must retain the full circle.
    gaps[-1] = ordered[0] + 360 - ordered[-1]
    nearest = [min(gaps[i - 1], gaps[i]) for i in range(len(ordered))]
    return {
        "occupied_span_deg": 360 - max(gaps),
        "median_nearest_neighbor_deg": median(nearest),
    }


def bearing_axis_ratio(bearings):
    """Unit-bearing transverse direction second moment eigenvalue ratio in [0, 1]."""
    n = len(bearings)
    if not n:
        return None
    c2 = sum(math.cos(2 * math.radians(b)) for b in bearings) / n
    s2 = sum(math.sin(2 * math.radians(b)) for b in bearings) / n
    anisotropy = min(1.0, math.hypot(c2, s2))
    return (1 - anisotropy) / (1 + anisotropy)


def selfcheck():
    assert angular_metrics([359, 1]) == {
        "occupied_span_deg": 2,
        "median_nearest_neighbor_deg": 2,
    }
    assert angular_metrics([0, 0])["median_nearest_neighbor_deg"] == 0
    assert angular_metrics([0, 0])["occupied_span_deg"] == 0
    assert angular_metrics([0, 90, 180, 270])["occupied_span_deg"] == 270
    assert math.isclose(bearing_axis_ratio([0, 90]), 1)
    assert math.isclose(bearing_axis_ratio([0, 180]), 0)
    assert quantile([0, 10], 0.9) == 9
    lon, lat, _ = GEOD.fwd(0, 0, 90, 100)
    assert math.isclose(GEOD.inv(0, 0, lon, lat)[2], 100, abs_tol=1e-8)


def analyze(rows, evidence):
    sites = []
    flat_points = []
    member_diagnostics = []
    for row, saved in zip(rows, evidence["sites"], strict=True):
        assert row["site_id"] == saved["site_id"]
        inputs = saved["saved_inputs"]
        measurements = inputs["measurements.json"]["measurements"]
        diagnostics = inputs["simulation-diagnostics.json"]
        observer = inputs["truth.json"]
        assert float(row["latitude_deg"]) == observer["latitude_deg"]
        assert float(row["longitude_deg"]) == observer["longitude_deg"]
        points = []
        for group in diagnostics["groups"]:
            if not group["detected"]:
                continue
            index = group["observation_index"]
            measurement = measurements[index]
            if measurement["object_type"] != "physical_aid":
                continue
            record = saved["joined_catalog_records"][
                group["representative_catalog_key"]
            ]
            assert record["normalized_type"] == "physical_aid"
            azimuth, _, distance = GEOD.inv(
                observer["longitude_deg"],
                observer["latitude_deg"],
                record["longitude_deg"],
                record["latitude_deg"],
            )
            noiseless = azimuth % 360
            point = {
                "site_id": row["site_id"],
                "observation_index": index,
                "representative_object_id": group["representative_object_id"],
                "representative_catalog_key": group["representative_catalog_key"],
                "physical_entity_id": record["physical_entity_id"],
                "latitude_deg": record["latitude_deg"],
                "longitude_deg": record["longitude_deg"],
                "local_east_m": distance * math.sin(math.radians(azimuth)),
                "local_north_m": distance * math.cos(math.radians(azimuth)),
                "range_m": distance,
                "noiseless_bearing_deg": noiseless,
                "observed_bearing_deg": measurement["bearing_deg"],
                "bearing_residual_deg": (
                    (measurement["bearing_deg"] - noiseless + 180) % 360
                )
                - 180,
                "merged_member_ids": group["member_object_ids"],
                "merged_catalog_keys": group["catalog_member_keys"],
                "merged_physical_ids": group["catalog_member_physical_ids"],
            }
            assert abs(point["bearing_residual_deg"]) <= 0.5 + 1e-8
            points.append(point)
        n = len(points)
        assert n == row["positive_marker_reports"]
        assert n == len({p["observation_index"] for p in points})
        assert n == len({p["representative_catalog_key"] for p in points})
        assert n == len({(p["latitude_deg"], p["longitude_deg"]) for p in points})
        pairwise = []
        nearest = [math.inf] * n
        for i, point in enumerate(points):
            for j in range(i):
                other = points[j]
                distance = GEOD.inv(
                    point["longitude_deg"],
                    point["latitude_deg"],
                    other["longitude_deg"],
                    other["latitude_deg"],
                )[2]
                pairwise.append(distance)
                nearest[i] = min(nearest[i], distance)
                nearest[j] = min(nearest[j], distance)
        for point, distance in zip(points, nearest, strict=True):
            point["nearest_neighbor_distance_m"] = (
                distance if math.isfinite(distance) else None
            )
        ranges = [p["range_m"] for p in points]
        true_bearings = [p["noiseless_bearing_deg"] for p in points]
        observed = angular_metrics([p["observed_bearing_deg"] for p in points])
        noiseless = angular_metrics(true_bearings)
        member_keys = sorted(
            {
                key
                for point in points
                for key in point["merged_catalog_keys"]
                if saved["joined_catalog_records"][key]["normalized_type"]
                == "physical_aid"
            }
        )
        members = [saved["joined_catalog_records"][key] for key in member_keys]
        close_pairs = []
        member_minimum = math.inf
        for i, first in enumerate(members):
            for second in members[:i]:
                distance = GEOD.inv(
                    first["longitude_deg"],
                    first["latitude_deg"],
                    second["longitude_deg"],
                    second["latitude_deg"],
                )[2]
                member_minimum = min(member_minimum, distance)
                if distance < 1:
                    close_pairs.append(
                        {
                            "first_key": first["key"],
                            "second_key": second["key"],
                            "distance_m": distance,
                            "first_latitude_deg": first["latitude_deg"],
                            "first_longitude_deg": first["longitude_deg"],
                            "second_latitude_deg": second["latitude_deg"],
                            "second_longitude_deg": second["longitude_deg"],
                        }
                    )
        member_diagnostics.append(
            {
                "site_id": row["site_id"],
                "physical_aid_members_in_detected_aid_report_groups": len(members),
                "minimum_member_pair_distance_m": member_minimum
                if math.isfinite(member_minimum)
                else None,
                "member_pairs_below_1cm": sum(
                    p["distance_m"] < 0.01 for p in close_pairs
                ),
                "member_pairs_below_1m": len(close_pairs),
                "pairs_below_1m": close_pairs,
            }
        )
        summary = {
            "site_id": row["site_id"],
            "port": row["port"],
            "status": row["status"],
            "detected_aid_count": n,
            "total_reports": row["total_reports"],
            "search_s": row.get("search_s"),
            "search_process_s": row.get("search_process_s"),
            "total_s": row.get("total_s"),
            "evaluated": row.get("evaluated"),
            "pruned": row.get("pruned"),
            "pruned_fraction": row["pruned"] / row["evaluated"]
            if row.get("evaluated")
            else None,
            "position_error_m": row.get("position_error_m"),
            "median_nearest_neighbor_distance_m": median(nearest) if n > 1 else None,
            "minimum_pairwise_distance_m": min(pairwise) if pairwise else None,
            "representative_pairs_below_1cm": sum(d < 0.01 for d in pairwise),
            "representative_pairs_below_1m": sum(d < 1 for d in pairwise),
            "median_pairwise_distance_m": median(pairwise) if pairwise else None,
            "median_observer_range_m": median(ranges) if ranges else None,
            "observer_range_90pct_m": quantile(ranges, 0.9) if ranges else None,
            "observed_angular_occupied_span_deg": observed["occupied_span_deg"],
            "observed_median_angular_nearest_neighbor_deg": observed[
                "median_nearest_neighbor_deg"
            ],
            "noiseless_angular_occupied_span_deg": noiseless["occupied_span_deg"],
            "noiseless_median_angular_nearest_neighbor_deg": noiseless[
                "median_nearest_neighbor_deg"
            ],
            "bearing_axis_eigenvalue_ratio": bearing_axis_ratio(true_bearings),
            "detected_report_merged_member_count": sum(
                len(p["merged_member_ids"]) for p in points
            ),
            "physical_aid_members_in_detected_aid_report_groups": len(members),
            "member_pairs_below_1cm": member_diagnostics[-1]["member_pairs_below_1cm"],
            "member_pairs_below_1m": len(close_pairs),
            "minimum_member_pair_distance_m": member_minimum
            if math.isfinite(member_minimum)
            else None,
            "max_abs_bearing_residual_deg": max(
                abs(p["bearing_residual_deg"]) for p in points
            ),
            "observer_latitude_deg": observer["latitude_deg"],
            "observer_longitude_deg": observer["longitude_deg"],
            "attempt_directory": saved["attempt_directory"],
        }
        sites.append({**summary, "detected_aid_report_origins": points})
        flat_points.extend(points)
    (HERE / "aid-clustering-nearcoincident.json").write_text(
        json.dumps(
            {
                "author": "J.C. Vaught",
                "scope": "Distinct canonical physical-aid members of detected physical-aid report groups; includes angular-merged nonrepresentative members",
                "sites": member_diagnostics,
            },
            indent=2,
        )
        + "\n"
    )
    data = {
        "author": "J.C. Vaught",
        "analysis_scope": "Post hoc descriptive geometry of saved detected physical-aid report representative origins; no search or simulation executed",
        "results_sha256": hashlib.sha256(RESULTS.read_bytes()).hexdigest(),
        "catalog_evidence": evidence["catalogs"],
        "sites": sites,
    }
    (HERE / "aid-clustering.json").write_text(json.dumps(data, indent=2) + "\n")
    with (HERE / "aid-clustering.csv").open("w", newline="") as stream:
        summaries = [
            {k: v for k, v in site.items() if k != "detected_aid_report_origins"}
            for site in sites
        ]
        writer = csv.DictWriter(
            stream, fieldnames=list(summaries[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(summaries)
    with (HERE / "aid-clustering-points.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(flat_points[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {k: json.dumps(v) if isinstance(v, list) else v for k, v in p.items()}
            for p in flat_points
        )
    return sites


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-evidence", action="store_true")
    args = parser.parse_args()
    selfcheck()
    rows = json.loads(RESULTS.read_text())
    evidence = (
        refresh_evidence(rows)
        if args.refresh_evidence
        else json.loads((HERE / "aid-clustering-evidence.json").read_text())
    )
    sites = analyze(rows, evidence)
    print(
        json.dumps(
            {
                "sites": len(sites),
                "detected_aid_reports": sum(s["detected_aid_count"] for s in sites),
                "all_counts_match_saved_suite": True,
                "all_bearing_residuals_within_0.5_deg": True,
            }
        )
    )


if __name__ == "__main__":
    main()
