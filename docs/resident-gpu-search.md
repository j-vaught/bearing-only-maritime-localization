# Resident GPU search with limited synthetic verification

The resident path is separate from the earlier CPU-limited coastwise search. The kernels have compiled and the approved small synthetic suite passed 28 checks on an NVIDIA RTX 6000 Ada. This path has not been profiled with a CUDA activity timeline or used for a coast-wide localization result. No coast-wide speed or accuracy result is claimed.

The CPU prepares the immutable map and catalog once. Preparation clips the source map through the existing map adapter, projects catalog coordinates, preserves uncertainty and identity metadata, and constructs balanced spatial trees. Each selected device receives its own arrays. This preprocessing is outside the iterative search and must be timed separately.

Once preparation finishes, the CPU assigns batches of root-cell identifiers to available devices. Each device generates those cells, classifies and prunes them, subdivides survivors, predicts visible objects, groups circular bearings and matches trusted chart-aid detections. The arrays used in these calculations remain on that device. Only aggregate counters, periodic frontier snapshots and terminal result files return to the host. File I/O, kernel launches and task scheduling still require CPU activity. Zero CPU activity is not a valid execution claim.

## Source path for each operation

| Operation | Implementation | Execution location by construction |
| --- | --- | --- |
| Immutable input preparation | `resident/preparation.py` | CPU, before traversal |
| Initial root-cell generation | `seed_cells` | CUDA |
| Land, water and unknown classification | `inside`, `covers_box`, `point` | CUDA |
| Nearby-object queries | Object-tree traversal in `prune_cells` and `predict_groups` | CUDA |
| Whole-cell marker pruning | `prune_cells` | CUDA |
| Whole-cell land-blocking witness | `barrier` | CUDA |
| Survivor compaction and subdivision | CuPy masks and `ResidentDevice.split` | CUDA device arrays |
| WGS84 candidate inverse projection and ellipsoidal bearings | `inverse_utm`, `geodesic` | CUDA |
| Candidate-to-object visibility | `ray` | CUDA |
| Circular grouping and frontmost representative selection | `predict_groups` | CUDA |
| Injective assignment and residual objective | `batched_hungarian` | CUDA |
| Root-task scheduling, kernel launches and output | `run_resident_search` | CPU coordination and I/O |

The old `GpuShorelineIndex`, `build_region_evidence`, `predict_visible_groups`, and CPU assignment functions are not called by the resident traversal. The existing Hungarian CUDA source is used directly, without its wrapper that reconstructs assignments on the host. There is no missing-CUDA fallback. A missing runtime or solver failure stops this path visibly.

## Correctness rules

Only typed chart-aid detections create deterministic matching obligations. Additional vessel detections do not create chart-aid obligations. Missing vessels and undetected chart aids never exclude a location. All surviving cells continue to the declared terminal size. There is no top-N cutoff. Scores apply only to the centers of terminal cells; no center score eliminates the rest of its cell.

This first resident path scores chart-aid observations only. It does not add an AIS likelihood term. Association uniqueness is not evaluated; a consistent center is not a unique fix.

Known map coverage and chart completeness remain independent. Unknown map areas remain unknown. Projection, position, marker reliability, physical identity, and bearing-bound certificates must be supplied explicitly. Uncertain record positions are not turned into exact positions. Duplicate physical identities remain ambiguous. Whole-cell land proofs require strict interior containment. Land-blocking rectangles are sufficient witnesses and do not claim to prove all possible occlusions.

Boundary coincidences, too many ray crossings, excessive object counts, numerical failure and unknown geometry produce unresolved outcomes or explicit errors. The code never silently discards objects to fit a buffer. It currently supports EPSG:32618, at most 256 marker reports, a configurable nearby-object capacity and a configured workspace budget. Source map and catalog preparation includes CPU geometry and coordinate projection.

The corrected default sensor uses Gaussian-shaped noise truncated at +/-0.5 degrees, with an underlying standard deviation of 1/6 degree. The hard 0.5-degree bound, rather than the standard deviation, supplies the marker tolerance. Older saved measurements used unbounded noise and must not be reused under this bound. The `numerical_validation_accepted` switch defaults to false and disables hard marker pruning and conclusive marker scores until numerical validation is accepted. It is an assertion, not an automatically verified certificate. Dataset coverage and identity uncertainty can still prevent conclusive pruning after the kernels are verified.

## Verification sequence requiring approval

First, inspect and compile the new files and run only the synthetic map-query and coordinate checks in `tests/test_resident_gpu_search.py`. These compare points, holes, coverage, unknown regions, rays, projection and bearings with independent references. No Charleston search is part of that first step.

After that step passes and receives review, run the marker-matching and domain-traversal checks. The matching check patches CPU geometry and assignment functions to raise if called during GPU scoring. Add stress cases for capacity limits, geometric degeneracy, distinct-object association and device scheduling before using the path for a result.

Next, approve a small profiled synthetic search. Capture a CUDA timeline and GPU utilization, with preprocessing, file output and active search reported separately. CUDA event spans alone include launch gaps and do not establish kernel occupancy or prove that all wall time is GPU computation. Runtime evidence must accompany the source-level execution table before this path is described as verified.

Finally, obtain approval for the full Eastern Seaboard Charleston run using the corrected measurement frame. Truth remains separate and is used only for evaluation after the search produces its outputs.

The CLI `tools/run_resident_marker_search.py` requires an explicit `--execute` flag to initialize inputs and run. Without it, the CLI only prints its static execution contract. The CLI has not been used to start a coast-wide search. Further profiling and search runs require user approval before execution.

## Output and recovery limits

Each root task writes all terminal center records and periodic device-frontier snapshots. The manifest identifies every catalog row and all search assumptions. A summary reports completed roots, total evaluated regions and elapsed time. It does not manufacture a location fix or an accuracy result.

Automatic resume is not implemented. A crash may leave an earlier frontier snapshot; the snapshots are diagnostic artifacts and are not a guaranteed transactionally resumable checkpoint. Failed runs must not be described as complete. The implementation still requires broader geometric and numerical checks and representative profiling before a full search.

## Approved synthetic evidence on October 28, 2026

The final focused command was uv run pytest -q tests/test_resident_gpu_search.py --basetemp /tmp/resident-gpu-small-proof --tb=short. It passed 28 checks after correcting a runtime-header compilation issue, rejecting nonboolean certificate flags, and adding explicit numerical margins at range and bearing cutoffs. Near-tied representative ranges and near-zero distances remain unresolved. The final bearing gate combines primitive error bounds using a factor of three because it includes both a representative bearing and a two-bearing group extent. These tests do not certify universal numerical bounds.

The instrumented tiny traversal evaluated 21 regions, retained and scored all 16 terminal cells, and recorded CUDA launches for seeding, pruning, prediction, coverage and Hungarian assignment. Its CPU query guards recorded zero calls. The observed record is stored in docs/verification/resident-gpu-small-test.json. The test deliberately uses CPU reference calculations outside the search to check device outputs.

Two independent source reviews found no CPU geometry, bearing, grouping or assignment fallback in the resident traversal. Both confirmed CPU activity for input preparation, scheduling, synchronization, aggregate counters and result output. Full terminal arrays and periodic frontiers are synchronously copied to the host for writing; boolean compaction can also synchronize. Prediction and matching include serial work within each candidate GPU thread, so residence on a GPU does not establish efficient use of the device.

Nsight Systems and Nsight Compute were not found on the server. The saved evidence is instrumented dispatch plus CPU query guards, not a CUDA activity timeline, CPU utilization measurement or coast-wide performance benchmark. No Charleston or full Eastern Seaboard search was launched in this verification.

## Preparation cache and stopping-rule correction awaiting approval

The recorded coarse run explicitly set both initial_size_m and terminal_size_m to 10000. That caused the engine to treat the first cells as terminal and finish without the intended finer subdivision. The engine already contains a survivor-subdivision loop; a smaller terminal size lets that loop continue automatically in one run. No extra stage launch is needed between levels. Unknown regions and inconsistent center samples must still retain their full cells under the existing rules.

Persistent preparation caching is now written but has not been executed or tested. The CLI uses .cache/resident-world by default. The key includes map and catalog content hashes, preparation options, preparation source fingerprints and geometry-library versions. Measurement inputs and search resolution are excluded from the key. A matching entry loads the prepared arrays without constructing VectorMap, reprojecting catalog records or rebuilding spatial trees. A change to relevant inputs selects a different entry. A corrupt entry fails visibly. Writes use an atomic rename and an interprocess lock prevents duplicate preparation for the same key.

The previous run never saved its prepared world and its process has exited. Consequently the first approved cache build must prepare that world once more. Later matching invocations reuse the saved arrays. File reading, cache validation and transferring buffers to a newly created GPU process remain necessary; disk caching is not a persistent GPU service.

The CLI now refuses equal initial and terminal cell sizes unless --coarse-only is explicitly passed, and logs the initial and stopping resolutions before preparation. The written checks in tests/test_resident_cache.py cover exact cache roundtrip, skipping the builder on a hit, input/settings invalidation and the coarse-only guard. Those new checks have not been run. No new search or cache build was launched while writing this correction. Automatic restart from a saved frontier remains separate and is not implemented.
