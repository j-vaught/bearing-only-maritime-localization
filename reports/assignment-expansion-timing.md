# Assignment expansion timing instrumentation

**Scope**

The assignment expansion API now accepts the optional keyword `collect_timing=False`. The default path leaves solver inputs, branch order, stopping rules, statuses, counters, callbacks, and branch event payloads unchanged. Timing metadata is returned only when collection is enabled through `ExpansionResult.stage_timings`.

**Recorded stages**

The per-mode aggregate metadata contains `pool_preparation_wall_s`, `frozen_target_preparation_wall_s`, `solver_fit_wall_s`, `residual_call_count`, `residual_wall_s`, and `canonical_scorer_wall_s`. Pool preparation includes seed preparation, candidate-pool construction, and deterministic branch-plan construction. Frozen-target preparation covers the candidate-specific target and sigma construction. Solver fit time covers the complete bounded SciPy solve. Residual time is nested inside solver fit time and must not be summed as an additional stage. Canonical scorer time covers only the post-solve branch evaluation, not seed validation.

The implementation uses monotonic CPU `perf_counter` calls only while collection is enabled. No GPU clock, scorer production instrumentation, truth input, held-out input, or benchmark run is involved. Timing is aggregate metadata rather than per-event output, so branch event hashes and event payloads remain unchanged.

**Verification**

The focused timing suite covers disabled-default parity, enabled metadata, zero-branch results, maximum-evaluation behavior, invalid-water canonical timing, callback exception propagation, and keyword type validation. The existing assignment expansion suite remains the behavioral authority. Timing overhead is not measured by this change, and these diagnostics must be collected only after the active coarse benchmark releases its lock.

J.C. Vaught
