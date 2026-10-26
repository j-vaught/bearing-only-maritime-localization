# Real input data

The current normalized catalog is `catalogs/accuracy-inputs-v2/east-coast-fullcoast-catalog-v2.json.gz`. It contains 66,668 source records. Its manifest records compressed and raw hashes, 2,268 AIS rows, and 64,400 chart-aid components.

The current map is `maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz`. The adjacent manifest records hashes, coverage and source provenance. Coordinates are geographic EPSG:4326; the example search projects into EPSG:32618. Unknown map coverage remains explicit.

Run `uv run python examples/unpack_east_coast.py` from the repository root to verify and unpack these inputs into `runs/east-coast/`.

The `ais/` CSV files retain Chesapeake and East Coast positions from the NOAA 2023-07-15 one-minute snapshot. MMSI and vessel names were omitted during the original extraction; row IDs are snapshot keys. The `chart-aids/` CSV files preserve NOAA ENC feature types, chart identifiers and available source attributes. Their adjacent manifests retain acquisition details and hashes. Raw chart rows can represent the same physical feature at several scales.

The small legacy Chesapeake raster mask remains under `maps/` for older algorithm experiments. The current version-2 example uses the vector map instead. Port anchor tables remain under `sites/`.

Sources are [NOAA historical AIS](https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/AIS_2023_07_15.zip) and [NOAA ENC Direct](https://nauticalcharts.noaa.gov/learn/encdirect/). Some source manifests name historical server paths; those are provenance, not required local paths. The unpack helper uses only files inside this repository.
