# East Coast pruning experiment

This experiment runs B1–B5 against the full 2023-07-15 12:00 UTC AIS snapshot and
deduplicated NOAA chart-aid catalog. It applies water-only observer candidates,
a 10 km range gate, land-blocked raster rays, and complete-report scoring.
Catalog objects within range and clear line of sight are grouped using the
simulator's 0.1 degree circular angular-resolution rule. A missing predicted
group incurs a penalty; extra detections may remain unmatched.

The synthetic simulator scene is built from every record in the coast-wide
catalog, so the complete-report assumption is true by construction. This differs
from the older Chesapeake-only scene, whose B1 run had 23 reports while the full
catalog predicts 138 resolvable reports at that location; that older case cannot
test complete-report penalties consistently.

The water mask uses Natural Earth 1:10m land polygons on a 500 m grid. It is a
coarse experimental land mask, not a navigation chart. The 10 km global grid and
256-candidate beam recover B1–B3 within 250 m, but B4 and B5 converge to distant
basins. A local-window diagnostic centered on each known truth recovers all five
within 129 m, showing that the scoring model can refine a good starting region
while global beam selection remains unresolved. The local-window result uses the
answer to define its bounds and is not a deployable search result. Search times
range from 3.9 to 28.4 seconds, far above a 100 ms target. The CSV includes the
per-site results; config.json records settings and source hashes.

Reproduce with `uv run python tools/benchmark_eastern_seaboard_pruning.py`.
