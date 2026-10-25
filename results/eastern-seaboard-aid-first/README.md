# Chart-aid-first area-pruning experiment

This experiment screens 100 km map tiles using chart-aid observations, then searches each retained tile with all reports and the combined AIS plus chart-aid catalog. It scores water-valid points on a 10 km lattice, applying the 10 km range, East Coast raster line-of-sight, 0.1 degree angular grouping, and a complete-report penalty for visible chart-aid groups. The top 8 or top 32 tiles are retained. This top-K screen is heuristic. It is not a proven-safe branch-and-bound rule.

The simulator and search now use the same East Coast water mask. The B1–B5 runs use synthetic observations generated from the complete catalog, so complete reporting is true by construction.

| Tiles retained | True tile retained | Within 250 m | Total staged time for B1–B5 |
| --- | --- | --- | --- |
| 8 | 3/5 | 2/5 | 14.6, 2.3, 6.9, 4.5, 3.3 s |
| 32 | 4/5 | 3/5 | 52.3, 6.1, 22.4, 10.1, 6.7 s |

For comparison, the existing full-region hierarchical search took 28.4, 3.9, 11.2, 7.2, and 5.2 seconds for B1–B5. It was within 250 m for B1, B2, and B3. The eight-tile cascade was faster but less accurate, and the 32-tile cascade was slower in every case.

B4 is an informative failure. It produced 20 reports, 19 labelled as chart aids, so the failure is not simply an absence of aid observations. Ten bearings fell in one 45-degree sector and the largest angular gap was 110.5 degrees, which makes the geometry more ambiguous. More directly, the aid-only screen ranked B4 true tile 35th, so neither shortlist retained it. The closest water candidate sampled on the 10 km lattice in the true tile was 4.8 km from B4. At the known position, diagnostic scoring gives 16.94, while the best false estimate found by the 32-tile search scores 252.81. Lower is better. The correct pose is strongly supported by the scoring model when evaluated, but the coarse tile screen does not sample and retain that basin.

The full per-case tables and tile scores are in top8 and top32. Reproduce with uv run python tools/benchmark_aid_first_tile_pruning.py --tiles 8 or --tiles 32.
