# Philadelphia software profiling without administrator access.

J.C. Vaught.

Instruction-level source profiling succeeds without administrator access on the existing server account. The earlier hardware-counter denial remains valid for throughput, cache-hit, and hardware-stall metrics. A separate CLI configuration requesting only software-patched source metrics collected real Philadelphia instruction data successfully.

The initial aggregate-per-opcode metric probe was denied. The instruction-address source probe using inst_executed and thread_inst_executed_true succeeded. The production-kernel diagnostic then collected instruction counts, active and predicated-on thread counts, branch targets, and theoretical global and local memory-sector demand. Hardware performance counters were not requested.

Three batches of 64 saved terminal cells were replayed on GPU 0 using the original map, canonical catalog, frozen bearings, tolerances, and scoring implementation. The batches were selected deterministically from the first, middle, and last saved terminal-leaf files. They are diagnostic samples. A baseline replay and an instrumented replay both matched every saved output field exactly for all 192 cells. This includes coordinates, statuses, warnings, residuals, marker identities, and rejection reasons. No full coast-wide search, simulator regeneration, or new position estimate was performed. The separately completed full Philadelphia search retains its 2.060523 m error.

| Compiled kernel resource | Value |
|---|---:|
| Registers per thread | 90 |
| Thread-private local bytes per thread | 1,328 |
| Threads per block | 128 |
| Theoretical active blocks per multiprocessor | 5 |
| Theoretical active threads per multiprocessor | 640 |
| Hardware maximum threads per multiprocessor | 1,536 |
| Theoretical occupancy limit | 41.7% |

These resource limits come from the CUDA function attributes and occupancy API. They are static limits, not measured achieved occupancy. Thread-private local memory includes compiler-placed arrays and stack state; its presence alone does not establish register spilling.

| Saved batch | Cells | Candidate/object pairs | Mean active threads per warp instruction | Mean predicated-on threads per warp instruction |
|---|---:|---:|---:|---:|
| 0 | 64 | 24576 | 2.650 / 32 | 2.442 / 32 |
| 1 | 64 | 44608 | 3.550 / 32 | 3.280 / 32 |
| 2 | 64 | 37440 | 3.797 / 32 | 3.507 / 32 |

Mean active threads are calculated as the sum of thread_inst_executed divided by the sum of inst_executed across all instruction addresses. The predicated-on value substitutes thread_inst_executed_true. These are instruction-weighted software measurements. They are not GPU utilization, elapsed-time fractions, or hardware occupancy counters. The small active-lane counts support control-flow divergence and uneven ray traversal as optimization targets.

The kernel performs double-precision geodesic calculations and traverses shoreline bounding-volume trees with private stacks and intersection arrays. Rays take different paths and require different amounts of work. The resource footprint and active-lane measurements provide concrete evidence for examining traversal coherence and private working-state size. The measurements do not establish whether arithmetic throughput, memory latency, or device-memory bandwidth is the dominant elapsed-time limiter.

The theoretical memory-sector counts describe instruction demand. They do not measure actual cache misses or DRAM traffic. The 64-cell batches are smaller than the main search batches and can expose different grid-level occupancy and cache behavior. Instrumented durations include software-patching overhead and must not be substituted for normal search timing.

Both saved cache files retain identical hashes, sizes, and modification times across baseline and instrumented replay. GPU allocations were released after collection. The source-page exporter produced two identical tables per kernel. The analysis retains one verified copy and reconciles every aggregate source count against its raw-report kernel ID.

The attached files include the profiler report, raw and assembly-address CSV exports, derived metrics, compiled resources, reference and replayed arrays, cache fingerprints, and the exact collection command. The replay driver is tools/replay_philadelphia_batch.py at revision 2bafebe.

[NVIDIA source-metric and permission documentation](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html#compatibility) distinguishes instruction-level source metrics from hardware counters. The same permission exception is present in the installed Nsight Compute 2025.3.1 documentation.

The raw diagnostic run is /home/jvaught2/projects/bearing-only-maritime-localization/runs/philadelphia-software-profile-20261030T043545Z.
