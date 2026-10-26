# Assignment expansion timing instrumentation

**Scope**

The assignment expansion API accepts the optional keyword \`collect_timing=False\`. The default path leaves solver inputs, branch order, stopping rules, statuses, counters, callbacks, and branch event payloads unchanged. Timing metadata is returned only when collection is enabled through \`ExpansionResult.stage_timings\`.

**Recorded stages**

The per-mode aggregate metadata contains \`pool_preparation_wall_s\`, \`frozen_target_preparation_wall_s\`, \`solver_fit_wall_s\`, \`residual_call_count\`, \`residual_wall_s\`, \`canonical_scorer_wall_s\`, \`seed_validation_wall_s\`, \`progress_callback_wall_s\`, \`result_callback_wall_s\`, and \`uninstrumented_remainder_wall_s\`. Pool preparation includes seed preparation, candidate-pool construction, and deterministic branch-plan construction. Frozen-target preparation covers candidate-specific target and sigma construction. Seed validation covers the pre-expansion canonical seed check. Solver fit covers the complete bounded solve. Callback timers include callback bodies and durable logging, and \`finally\` records elapsed work when a callback raises.

Residual time is nested within solver fit time and is not added again when computing the uninstrumented remainder. The remainder is elapsed mode time minus the non-overlapping pool, frozen-target, solver-fit, canonical-scorer, seed-validation, progress-callback, and result-callback stages. Residual call count is a count and is excluded from wall-time arithmetic.

The implementation uses monotonic CPU \`perf_counter\` calls only while collection is enabled. No GPU clock, scorer production instrumentation, truth input, held-out input, or benchmark run is involved. Timing is aggregate metadata rather than per-event output, so branch event hashes and event payloads remain unchanged.

**Verification and bounded follow-up**

The focused timing suite covers disabled-default parity, enabled metadata, zero-branch results, maximum-evaluation behavior, invalid-water canonical timing, callback timing and exception propagation, seed-validation timing, and keyword type validation. The existing assignment expansion suite remains the behavioral authority. Timing overhead is not measured by this change.

A later bounded stage profile should use only score-only modes already selected by the frozen search artifact. It should bind the source and input hashes recorded by that artifact, acquire \`.agent-work/benchmark.lock\`, verify at least 40 GiB free before starting, cap wall time at 120 seconds, and write aggregate stage metadata only. Mode selection must not use evaluator errors, truth, or held-out results. The profile should stop on the deadline or lock/resource violation and make no accuracy or acceptance claim. It must wait until the active coarse benchmark releases its lock.

J.C. Vaught
