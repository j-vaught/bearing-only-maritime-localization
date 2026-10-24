# Eastern Seaboard map set

This four-page, oversized map set contains the July 15, 2023 AIS snapshot, NOAA charted navigation aids, both layers together, and B1–B5 observer locations. The PDF is vector-based for zooming and print. PNGs are page previews.

## Contents

1. eastern-seaboard-maps.pdf — all four map pages.
2. east-coast-map-page-1.png — AIS positions grouped by reported vessel class.
3. east-coast-map-page-2.png — charted aids grouped into nine feature families.
4. east-coast-map-page-3.png — AIS and chart-aid layers together.
5. east-coast-map-page-4.png — B1–B5 on the coast extent, with a Chesapeake Bay inset and coordinates.
6. data/ — plot-ready tables, coastline geometry, and source/count manifest.

The AIS panel plots 2,268 vessel positions from the one-minute snapshot at 12:00–12:01 UTC on July 15, 2023. Vessel classes are broad groups made from AIS ship-type codes. The chart-aid panel plots 51,399 distinct locations after same-family points at the same rounded coordinates were deduplicated. Families are Beacon, Buoy, Daymark, Fog, Light, Radar, Radio, Retroreflector, and Topmark.

The map background uses Natural Earth 1:10m land polygons. It shows the land/water boundary but is not a street-level OpenStreetMap rendering. Source details and processing notes are recorded in data/manifest.json.

## Rebuild on the server

Run from the repository root on comech-2080:

```sh
python tools/prepare_eastern_seaboard_map_data.py
typst compile --root . figures/eastern-seaboard-maps.typ results/eastern-seaboard-maps/eastern-seaboard-maps.pdf
typst compile --root . --format png --pages 1-4 --ppi 100 figures/eastern-seaboard-maps.typ results/eastern-seaboard-maps/east-coast-map-page-{p}.png
```

The plot data and figure source are versioned in this repository so the files can be inspected or regenerated without relying on the local workstation.
