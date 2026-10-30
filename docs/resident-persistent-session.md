# Persistent GPU search workers.

J.C. Vaught.

Each sequential observation frame now uses the same GPU workers and uploaded map. The suite loads the existing prepared map and canonical object sidecar once on the host, then starts one search subprocess. That subprocess owns a dedicated thread and resident device instance for each selected GPU. The first request constructs and uploads the device inputs. Later requests reuse those same instances, streams, map arrays, catalog arrays, and GPU modules.

The reusable session binds to one prepared world object and one complete search configuration. A different world or configuration is rejected before request output is created. The prepared world must remain immutable throughout the session. Concurrent requests are rejected. Each accepted request creates fresh observation vectors, traversal frontiers, completion totals, output files, and profiling counters. Profiling wrappers bind directly to the retained raw modules, so repeated requests do not accumulate wrappers or mix timing records.

Starting root packets increase from 8 cells to 128 cells. For the saved 39,435-root coast rectangle, this reduces root packets from 4,930 to 309. The suite preserves the existing 512-cell processing limit and the 2 GB workspace ceiling. The change groups existing cells into larger work packets. It preserves all-root traversal, refinement, terminal-center retention, marker matching, warning flags, and numerical boundaries. It does not introduce descendant work stealing between GPUs.

The supervisor sends measured bearings, their bounds, and the output directory to the persistent worker. Observer truth remains outside that request protocol. The existing minimum-residual selector still runs after traversal, and its estimate is frozen before the accuracy evaluator reads truth. The supervisor performs simulation, scheduling, progress checks, and file handling. Geometric queries, bearing prediction, land visibility, pruning, and matching retain their existing GPU execution paths.

The process remains alive between eligible sites and releases its GPU allocations when the suite closes. A failed or timed-out request poisons the service and stops its process. The service does not silently restart and label newly uploaded buffers as reused. The resource guard permits the exact owned worker process while continuing to reject foreign GPU contexts. Search timeout and cell-count ceilings remain active.

Search records distinguish first-request initialization from subsequent reuse. Per-device diagnostics record construction, reuse, initialization duration, and owner-thread identity. Search duration includes the device initialization paid by the first request, traversal, and per-request lifecycle finalization. Shared disk-cache preparation, worker startup, simulation, saved-output selection, and accuracy evaluation remain separately recorded. Existing timings from the earlier runner remain historical measurements.

The saved map and object caches are read without rebuilding. Their SHA-256 hashes, sizes, and modification times are checked against the saved fingerprints. Source changes do not modify those cache artifacts.

Verification uses fake GPU devices and fake worker responses to check reuse, request isolation, profile separation, thread ownership, failure handling, timeout cleanup, resource ownership, and truth isolation. No localization search or CUDA execution is part of these checks. A new approved GPU run is required to measure actual speed and confirm end-to-end numerical equivalence.

The combined verification passed 48 CPU-only checks. The GPU test was explicitly deselected. Formatting, lint, and type checks passed on the nine changed Python files. The cache record is saved in [the verification report](verification/resident-persistent-cache-check.json).
