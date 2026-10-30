# Dynamic GPU scoring trial.

J.C. Vaught.

The regional Charleston test did not pass the predeclared performance gate. Five matched pairs used the same frozen 67-aid observations and preloaded maps on GPUs 0 through 2. Dynamic scoring retained exactly the same selected estimate and every terminal output array. Its median reduction was 3.83 percent, below the required 5 percent. One slower dynamic run raised its mean time. The coast-wide follow-up was not run, and the default scheduler remains fixed.

| Measure | Fixed | Dynamic |
|---|---:|---:|
| Median search time | 0.117808 s | 0.113299 s |
| Mean search time | 0.117268 s | 0.124838 s |
| WGS84 error | 0.264702 m | 0.264702 m |

The scope is the pre-existing regional Charleston rectangle, not the full Eastern Seaboard. It contains 725 initial cells at 1 km and refines to 1 m stopping size, yielding 78 terminal centers. Existing global coast-wide timings are therefore separate measurements. Two warmup searches preceded five measured pairs with alternating execution order. Timing excludes map loading, initial GPU preparation, saved-output selection, and posthoc accuracy evaluation.

Each dynamic trial handed 39 cells to a second idle GPU. The logical payload was 1,248 bytes; the two DMA legs moved 2,496 bytes in total. Copy time ranged from 0.101 to 0.279 ms in measured trials. No map or catalog arrays migrated. The donor and receiver each scored 39 cells, and the third GPU had no useful scoring work in this small case. Every migrated cell moved to another GPU at most once.

The prototype uses recent score milliseconds per cell and observed copy cost, requires a predicted saving of at least 2 ms, batches 32 to 256 cells, allows one packet per receiving GPU, applies a 2 ms cooldown, and caps total DMA traffic at 32 MiB per search. Root traversal stays on its original worker; the handoff targets the expensive terminal scoring stage. More general prediction or movement of active refinement frontiers is not implemented.

Direct peer transport failed bit-exact copy checks on this server even though peer capability was advertised. Both the library copy and explicit CUDA peer copy returned incorrect values. Pinned host staging passed all six directed GPU-pair checks. The accepted trial therefore uses asynchronous device-to-pinned-host transfer while the donor continues, followed by device upload on the receiver. Pinned buffers remain alive through completion. Host operations transport bytes and schedule work; map queries, pruning, visibility, matching, and residual calculations remain on GPUs.

The first implementation attempt failed during peer initialization before a search ran. A second attempt was rejected when transferred coordinates differed. Neither rejected attempt contributes to performance or accuracy claims. Their records are preserved in rejected-attempts.json, and the transport checks are in transfer-diagnostic.json.

The strict equivalence gate compares sorted cell coordinates, status codes, warning flags, residuals, matched object indices, rejection reasons, and the selected estimate. All accepted trials preserve these values exactly. The selected estimate is frozen before the WGS84 error evaluator reads truth. Saved map and catalog cache hashes, file sizes, and modification times are unchanged.

The tested source revision for the accepted run was 608de57. The raw run remains at runs/dynamic-charleston-gate-20261030T020707Z. The gate required identical results, a median speedup greater than 1.05, at least four wins in five pairs, and real handoffs. It won four pairs and performed five measured handoffs, but the speed criterion failed. No further performance search was launched after this result.

[CuPy transfer ordering](https://docs.cupy.dev/en/stable/reference/generated/cupy.asarray.html) and [stream/event ordering](https://docs.cupy.dev/en/stable/reference/generated/cupy.cuda.Stream.html) document the synchronization requirements used by the transport implementation.
