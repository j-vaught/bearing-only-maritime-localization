"""Extract a bounded, one-record-per-vessel snapshot from a NOAA daily AIS ZIP."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

NOAA_ARCHIVE_ROOT = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"
REQUIRED_COLUMNS = {"MMSI", "BaseDateTime", "LAT", "LON", "SOG", "COG", "VesselType"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Path to NOAA daily AIS ZIP.")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV path.")
    parser.add_argument("--manifest", type=Path, help="Optional JSON provenance path.")
    parser.add_argument(
        "--start-utc", required=True, help="Window start, ISO UTC without timezone suffix."
    )
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--min-lat", type=float, required=True)
    parser.add_argument("--max-lat", type=float, required=True)
    parser.add_argument("--min-lon", type=float, required=True)
    parser.add_argument("--max-lon", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = dt.datetime.fromisoformat(args.start_utc)
    if start.tzinfo is not None:
        raise ValueError("--start-utc must be UTC without an explicit timezone offset.")
    if args.duration_seconds <= 0:
        raise ValueError("--duration-seconds must be positive.")
    if args.min_lat >= args.max_lat or args.min_lon >= args.max_lon:
        raise ValueError("Bounding box minima must be below maxima.")
    end = start + dt.timedelta(seconds=args.duration_seconds)
    midpoint = start + (end - start) / 2

    selected: dict[str, dict[str, str]] = {}
    scanned = 0
    within_window_and_box = 0
    with zipfile.ZipFile(args.archive) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError("Expected exactly one CSV file in the NOAA ZIP archive.")
        with io.TextIOWrapper(
            archive.open(csv_names[0]), encoding="utf-8-sig", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                raise ValueError(f"NOAA CSV must include these fields: {sorted(REQUIRED_COLUMNS)}")
            for row in reader:
                scanned += 1
                timestamp = dt.datetime.fromisoformat(row["BaseDateTime"])
                latitude = float(row["LAT"])
                longitude = float(row["LON"])
                if not (
                    start <= timestamp < end
                    and args.min_lat <= latitude <= args.max_lat
                    and args.min_lon <= longitude <= args.max_lon
                ):
                    continue
                within_window_and_box += 1
                mmsi = row["MMSI"]
                previous = selected.get(mmsi)
                if previous is None or abs(timestamp - midpoint) < abs(
                    dt.datetime.fromisoformat(previous["BaseDateTime"]) - midpoint
                ):
                    selected[mmsi] = row

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "object_id",
        "timestamp_utc",
        "latitude_deg",
        "longitude_deg",
        "sog_kn",
        "cog_deg",
        "vessel_type_code",
    ]
    ordered = sorted(selected.values(), key=lambda row: row["MMSI"])
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for object_id, row in enumerate(ordered):
            writer.writerow(
                {
                    "object_id": object_id,
                    "timestamp_utc": row["BaseDateTime"],
                    "latitude_deg": row["LAT"],
                    "longitude_deg": row["LON"],
                    "sog_kn": row["SOG"],
                    "cog_deg": row["COG"],
                    "vessel_type_code": row["VesselType"],
                }
            )

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        source_date = start.date().isoformat()
        manifest = {
            "source": "NOAA Office for Coastal Management, Nationwide AIS Broadcast Points",
            "source_url": (
                f"{NOAA_ARCHIVE_ROOT}/{source_date[:4]}/AIS_{source_date.replace('-', '_')}.zip"
            ),
            "source_archive": args.archive.name,
            "source_csv_member": csv_names[0],
            "time_start_utc": start.isoformat() + "Z",
            "time_end_utc_exclusive": end.isoformat() + "Z",
            "selection": (
                "For each MMSI in the area and time window, retain the report "
                "nearest the window midpoint."
            ),
            "bounding_box_wgs84": {
                "min_latitude": args.min_lat,
                "max_latitude": args.max_lat,
                "min_longitude": args.min_lon,
                "max_longitude": args.max_lon,
            },
            "source_rows_scanned": scanned,
            "rows_in_time_and_area": within_window_and_box,
            "unique_vessels_selected": len(selected),
            "mmsi_and_vessel_names_in_output": False,
            "output_rows_are_ais_vessels_only": True,
        }
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"Scanned {scanned:,} archive rows; selected {len(selected):,} unique vessels "
        f"from {within_window_and_box:,} reports in the area and time window."
    )


if __name__ == "__main__":
    main()
