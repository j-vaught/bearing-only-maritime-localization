# Pairwise geometric-hypothesis pilot

This experiment compares deterministic pairwise bearing-ray intersections with the 1 km hierarchical baseline on three matched noise seeds at each of B1–B5. Both methods use simulated global bearings from the same NOAA AIS vessel layout and ENC chart-aid coordinates, the 10 km range limit, type labels, the 500 m water/land mask, and candidate-specific line-of-sight checks. The observer positions are hidden from search and used only for evaluation.

Pairwise search uses intersections of two observed bearing rays with two distinct catalog coordinates. It then scores a deterministic, bounded set of hypotheses against the full report set and refines the leading candidates. It returned no position for B3 because only one bearing was detected.

| Method | Attempts | Valid estimates | Successes | Median error on valid estimates | Median CPU search time | P95 CPU search time |
|---|---:|---:|---:|---:|---:|---:|
| Hierarchical 1 km | 15 | 15 | 9 | 51.8 m | 1,973 ms | 2,118 ms |
| Pairwise hypotheses | 15 | 12 | 9 | 25.9 m | 782 ms | 1,186 ms |

The overall median error above is calculated only from valid estimates. Pairwise latency above likewise uses its 12 valid estimates; the separate `overall-summary.csv` also reports latency over every attempt, including the immediate B3 underdetermined-input rejection. For a strictly paired comparison, `matched-case-summary.csv` contains only B1, B2, B4, and B5 trials where both methods returned estimates. Across those 12 cases, hierarchical search had a 49.0 m median error and 2,000 ms median runtime, while pairwise search had 25.9 m and 782 ms. Both methods met the 250 m / three-correct-AIS criterion in 9 of those 12 cases.

Success requires both position error at or below 250 m and at least three correct AIS associations. B4 estimates are within 16–50 m, but there is only one visible AIS vessel, so those trials do not satisfy the association requirement. The pairwise method's 9/15 success rate matches the baseline; its position estimates are more accurate on the trials where it produces a solution, while its median search time is lower in this run.

`trials.csv` contains per-seed outputs. `summary.csv` reports each site and method; `overall-summary.csv` reports aggregate metrics. `config.json` records the settings and seed rule. Re-run with:

```bash
uv run python tools/run_ais_enc_pilot.py --replicates 3
```

The run evaluates bearing synthesis and search only against known AIS/ENC point coordinates. It is not a benchmark of actual visual detections or a coast-wide workload. The separate [comech GPU scoring benchmark](gpu-benchmark.md) isolates the bearing-score kernel and does not include pair generation, map visibility, assignment, or full search.
