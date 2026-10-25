# Chart-aid-first area-pruning experiment

This experiment tests whether chart-aid observations can screen geographic areas before the search uses the combined AIS and chart-aid catalog. The first stage scores water-valid points on a 10 km lattice using aid-labelled reports only. It applies the 10 km range, 500 m raster line-of-sight, 0.1 degree angular grouping, and a complete-report penalty for visible chart-aid groups. It retains either the best 8 or best 32 100 km tiles. The second stage searches each retained tile with all reports and both catalog sources.

The screen is a top-K heuristic, not a mathematically safe branch-and-bound rule. It may discard the correct tile when the aid-only score ranks that tile below K. The experiment reports the true tile rank so this failure is visible.

| Retained tiles | True tile retained | Within 250 m | Total staged runtime, B1–B5 |
| --- | --- | --- | --- |
| 8 | 3 of 5 | 3 of 5 | 15.9, 2.5, 5.3, 4.8, 3.3 s |
| 32 | 5 of 5 | 4 of 5 | 50.8, 5.9, 19.8, 14.2, 7.8 s |

With 8 tiles, the true tile ranks were 1, 1, 25, 19, and 4 for B1 through B5. The shortlist therefore lost the true areas for B3 and B4. With 32 tiles, it kept all five true tiles, but B4 still produced a 705 km error. The best estimate within its true 100 km tile was 85.6 km from truth. This shows that retaining the right area is not sufficient; the local search or score can still select the wrong basin.

For comparison, the previous global hierarchical run took 28.4, 3.9, 11.2, 7.2, and 5.2 s for B1 through B5, respectively. The 8-tile cascade was faster for all five but had mixed accuracy. The 32-tile cascade was slower for all five and improved the number of cases within 250 m from three to four. Thus this fixed top-K version does not yet deliver both global accuracy and lower runtime.

These are seeded closed-loop synthetic cases. The simulator and search share the same coast-wide catalog, and the simulator reports every in-range, land-visible angular group. That assumption is favorable to complete-report scoring and may not hold for live sensors or incomplete source data. The full per-case tables and tile scores are in top8 and top32. Reproduce the runs with uv run python tools/benchmark_aid_first_tile_pruning.py using --tiles 8 or --tiles 32.
