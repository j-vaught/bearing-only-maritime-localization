# Resident search profiling

The old coast-wide run was stopped at the user's request, with its outputs preserved. Before its replacement, the corrected 64-report Charleston frame was profiled on the same canonical catalog and saved shoreline index. The measurement is stage profiling with CUDA events plus worker-thread Python cProfile, not a hardware occupancy measurement or a pure-kernel-busy timeline. Nsight executables were unavailable on this server.

The regional GPU0 preflight completed all 725 roots, evaluated 16,465 regions and produced 603 terminal centers. Every cell, status, flag, residual and matched catalog-row ID agrees exactly with the earlier regional result. The full search-loop time was 21.01 seconds under these profiling conditions. The earlier GPU3 measurement used a shared device, so these are not controlled before/after speed comparisons.

| Measured operation | Regional GPU0 stream span | Coast starting-grid summed device spans |
| --- | --- | --- |
| Region pruning | 14.196 s | 13.895 s |
| Prediction, including visibility and grouping | 4.737 s | 13.740 s |
| Cooperative marker assignment | 0.046 s | 0.426 s |

Prediction and assignment are nested inside scoring. Total regional scoring was 4.863 seconds; it must not be added to its prediction and assignment children. The disjoint regional root stages total 19.170 seconds of stream spans. Pruning accounts for approximately 74 percent of those measured spans. The main custom kernels are prune_chart_geometry and pp_pairs, which combine map/object geometry, true bearings, range and sight-line calculations. The profile does not separate every operation inside those kernels.

The coast starting-grid preflight completed all 39,435 roots on GPUs0–2 in 16.04 seconds of search-loop wall time. Its 30.617 seconds of summed device spans overlap across three devices and are not wall time. It rejected 38,295 regions and retained 1,146 terminal centers. Two near-equality floating-point cell-width cases caused tiny extra subdivisions, for 39,443 evaluated regions. This was a coarse-only profiling pass, not a localization accuracy result or a filter selecting the roots for the full run.

Wrapped regional NPZ writes consumed 0.054 seconds of host wall time. That counter excludes device-to-host copies, progress JSON, profiler JSONL and other output. cProfile attributes worker call elapsed time, including waits; it does not measure CPU busy cycles. Instrumentation has measurable overhead, especially span recording and file opens. The production profile samples every tenth whole batch and retains its nested children, with host call durations and CUDA event spans recorded separately.

The full replacement uses the original coast bounds and all 39,435 roots, devices0–2, initial 10-kilometer cells and a terminal maximum width of 1 meter. Every survivor continues, with no top-N cutoff. The same committed corrected observations are used. Both base and canonical-object caches must be hits; rebuilding is blocked. The selected estimate is frozen before evaluator truth is read. GPU3 remains outside the production search.

The [preflight measurements](verification/resident-search-profile-preflight.json) and [raw timing records, summaries and CPU profiles](verification/resident-search-preflight-profiles.zip) are saved for independent analysis. Full-run raw spans, output-write records, process CPU/I/O counters, GPU readings and root/batch progress are saved on the HDD under that run's job directory. CUDA event stream spans can include host launch gaps. Native array kernels are not all present in the custom-kernel counters; nested inclusive durations are not additive.
