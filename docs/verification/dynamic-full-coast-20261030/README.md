# Full-coast dynamic scoring comparison.

J.C. Vaught.

Dynamic terminal scoring reduced search time for all four tested slow sites while preserving every saved terminal array and the selected estimate. The experiment searches the entire eastern seaboard for each observation frame. It bypasses the earlier regional Charleston performance gate under explicit authorization.

| Site | Charting aids | Fixed mean, s | Dynamic mean, s | Time reduction | Error in both modes, m |
|---|---:|---:|---:|---:|---:|
| B17 Charleston | 67 | 0.852 | 0.853 | -0.2% | 0.856 |
| B5 Providence | 32 | 24.390 | 15.125 | 38.0% | 3.565 |
| B7 Bridgeport CT | 24 | 17.058 | 9.810 | 42.5% | 2.338 |
| B16 Wilmington NC | 24 | 81.776 | 49.147 | 39.9% | 0.742 |
| B11 Philadelphia | 18 | 264.552 | 173.899 | 34.3% | 2.061 |

Each site has two paired comparisons in alternating fixed/dynamic and dynamic/fixed order. The table reports means. With two observations per mode, these equal the medians. Two additional full-coast Charleston warmups are excluded. Every measured search uses GPUs 0 through 2 with maps preloaded before timing. The same resident session serves both modes within each site. Timing excludes map loading, GPU preparation, saved-result selection, terminal-output comparisons, and accuracy evaluation.

Each trial uses the exact saved observations from the earlier approved preloaded B-site suite. No simulator frame was regenerated. The search receives physical-aid bearings and tolerances. The selected result is saved and hashed before the independent evaluator reads truth and computes WGS84 error. Sorted terminal coordinates, status codes, warning flags, residuals, object associations, and rejection reasons match exactly between modes.

The full-domain configuration uses 10 km initial cells, a 1 m terminal stopping size, 512-cell batches, 128 roots per task, and a 10 km viewing range. Pruning, land visibility, deterministic scoring, tolerances, warnings, and catalog normalization are unchanged. Both saved map and canonical-object caches retain identical hashes, sizes, and modification times.

Dynamic sharing hands ready terminal-scoring batches to idle GPUs. It forecasts scoring time from recent milliseconds per cell and measured transfer cost, requires at least 2 ms predicted savings, sends 32 to 256 cells per packet, permits one outstanding packet per receiver, applies a 2 ms cooldown, and limits total DMA traffic to 32 MiB per search. Each transferred cell moves to another GPU once. Root traversal and refinement frontiers remain assigned to their original workers.

Maps and catalogs are resident on every GPU and never migrate. Candidate bounds use the validated pinned-host transport because direct peer transfers failed the earlier server copy checks. The host transports bytes and schedules packets. Map queries, pruning, visibility, matching, and residual calculations remain GPU computations. Reported candidate traffic includes both device-to-host and host-to-device legs. Summed packet copy durations are telemetry, not an additive critical-path timing.

| Site | First fixed scoring counts, GPU 0 / 1 / 2 | First dynamic scoring counts, GPU 0 / 1 / 2 | Dynamic DMA MB, repeat 1 / 2 |
|---|---|---|---|
| B17 | 0 / 0 / 171 | 171 / 0 / 0 | 0.000 / 0.000 |
| B5 | 22647 / 0 / 117 | 6540 / 10769 / 5455 | 0.760 / 0.759 |
| B7 | 354 / 22495 / 8 | 6180 / 10872 / 5805 | 0.744 / 0.799 |
| B16 | 191 / 251 / 81292 | 23166 / 38003 / 20565 | 2.770 / 2.553 |
| B11 | 7929 / 104505 / 298766 | 99426 / 160885 / 150889 | 9.830 / 8.775 |

Charleston performs no terminal handoffs because no eligible peer is idle when its terminal batch is ready. Its measured time is effectively unchanged. The heavier cases provide work after other GPUs finish their root traversal, allowing useful scoring handoffs. Counts describe executed terminal scoring and do not measure equivalent computational cost per cell.

The dynamic scheduler remains opt-in. This experiment establishes the benefit for these four slow observation frames. Full observations, estimates, freezes, accuracy records, timing records, packet telemetry, and sampled profile summaries accompany this report. Raw terminal leaves and full profiling spans remain in the saved HDD run.

The tested implementation is revision 1d1680c88d10c2dfa9468d3aab28ac70aef21a8f. The reproducible driver is tools/benchmark_dynamic_coastwide.py. The raw run is /home/jvaught2/projects/bearing-only-maritime-localization/runs/dynamic-coastwide-comparison-20261030T033953Z.
