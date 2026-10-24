# Chesapeake AIS and chart-aid pilot inputs

This directory contains the compact, versioned inputs for the B1–B5 search experiments. The checked-in files are the exact selected data used by the experiment, so reproduction does not depend on downloading a changing service response.

## AIS snapshot

`ais/chesapeake_2023-07-15T1200Z.csv` contains 372 vessel positions selected from the NOAA Office for Coastal Management nationwide AIS archive for 2023-07-15 12:00:00–12:01:00 UTC. For each MMSI within the experiment bounds, the report nearest the interval midpoint was retained. MMSI and vessel names are omitted; `object_id` is a stable row identifier for this selected snapshot. Source archive: [NOAA nationwide AIS archive for 2023-07-15](https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/AIS_2023_07_15.zip). The source and selection details are in `ais/chesapeake_2023-07-15T1200Z.manifest.json`.

`ais/eastern-seaboard_2023-07-15T1200Z.csv` contains 2,268 AIS positions selected from the same one-minute interval over longitude −82° to −66° and latitude 24.5° to 45.5°. It is the larger catalog used in the coast-wide GPU scaling experiment. The output preserves AIS vessel-type codes but omits MMSI and vessel names. The extraction scanned the same nationwide source archive and retained the report nearest the interval midpoint for each MMSI in the bounding box. Provenance and selection details are in `ais/eastern-seaboard_2023-07-15T1200Z.manifest.json`.

## NOAA ENC chart aids

`chart-aids/chesapeake-enc-aids.csv` contains 427 unique point features inside longitude −76.8° to −75.5° and latitude 36.5° to 39.5°, selected from NOAA Electronic Navigational Chart (ENC) GIS layers for S-57 object classes 7, 9, and 17. The file preserves the source object-layer code, feature identifier, and chart name. Duplicate coordinates were removed at seven decimal places. NOAA distributes free ENC data and provides an ENC GIS service; see [NOAA ENC Direct to GIS](https://nauticalcharts.noaa.gov/learn/encdirect/) and [ENC data and services](https://www.nauticalcharts.noaa.gov/data/gis-data-and-services.html).

`chart-aids/eastern-seaboard-enc-aids.csv` contains 64,400 NOAA ENC Direct to GIS point-aid rows in the East Coast bounding box, covering Florida through Maine and adjacent waters. It includes Overview, General, Coastal, Approach, Harbour, and Berthing scale bands. The data preserves each feature's original service layer and class, source chart cell (`DSNM`), source object ID, coordinates, and returned chart attributes. `chart-aids/eastern-seaboard-enc-aids.manifest.json` records the live-service retrieval time, region bounds, per-layer counts, and checksum. This snapshot is the full coast-wide aid catalog for the current experiment; it is separate from the older Chesapeake-only input above.

Features from different ENC scale bands can repeat at different levels of detail. NOAA recommends extracting one scale band per scale-specific GIS dataset. The raw file therefore retains each scale band, and the coast-wide search diagnostic forms a deduplicated catalog by object-layer class and coordinates rounded to five decimal places. That catalog contains 51,416 entries across all scales; the Approach-only catalog contains 11,248 entries.

## Water and land mask

`maps/chesapeake-water-mask-500m.npz` is the exact 500 m binary water mask used for candidate-water checks and raster line-of-sight. The compressed file stores the Boolean `water` array, projected southwest origin, cell size, and coordinate reference string. The adjacent JSON records its projection, source-map extent, and color-classification rule. This is a coarse experiment mask derived from NOAA ENC portrayal colors; it is not a navigational chart.

NOAA federal data are generally reusable in the United States; attribution is retained here to identify the sources and transformations. See [NOAA's chart FAQ](https://nauticalcharts.noaa.gov/learn/faq.html) and [NOAA copyright information](https://oceanservice.noaa.gov/about/faq.html).

## Reproduction

Use these committed inputs together with the experiment configuration and runner under `results/ais-b1-b5-pilot-v1/with-chart-aids/`. No MMSI-level raw archive is required. The scenario uses synthetic bearing observations generated from the snapshot and chart-aid coordinates; it is not a record of actual visual detections.

The earlier coast-wide GPU scoring run combines the East Coast AIS catalog with only the Chesapeake ENC aid points and times candidate scoring only. The current `results/eastern-seaboard-search/` experiment uses the full coast-wide chart-aid snapshot and five search methods, but still has no coast-wide land mask or line-of-sight filtering. Its result notes describe the comparison, limitations, and reproduction steps.
