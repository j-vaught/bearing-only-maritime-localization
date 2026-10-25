# Chart-aid-first tile screening experiment

The first stage scores water-valid candidate points on a 10 km lattice using only aid-labelled reports and chart aids. It applies the 10 km range, land line-of-sight, 0.1 degree angular grouping, and complete-report penalty. The best point score ranks each 100 km tile; top-ranked tiles are retained for refinement. The second stage uses all reports and the combined AIS plus chart-aid catalog inside each tile, with a 10 km catalog buffer, and refines each region hierarchically.

The seeded B1–B5 runs are closed-loop synthetic cases. Results report true-tile rank and retention, final error, and staged runtime beside the previously measured global hierarchical baseline. See tile-details.json for per-tile scores. Reproduce with uv run python tools/benchmark_aid_first_tile_pruning.py --tiles 8 or --tiles 32.
