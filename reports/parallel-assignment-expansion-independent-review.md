# Independent review of the whole-mode CPU pool

This final review covers committed pool `b76deb56626e8b5d2a908b748ac80689ca66f77c` and committed runner `a41570c525f4da6b801622366c5a601bfba09c22`. The producer files were not modified by this review.

The independent fixture uses four distinct per-mode input digests, a shared package commit, a shared static source digest, and a real `CandidateScorer` constructed from a small WGS84 vector-map fixture. It runs four spawned workers with a 30 second local deadline and compares every mode against an independent serial `expand_assignments` result. The comparison covers status, completion, budget state, all counters, best hypothesis, and branch-event payloads. It checks unique worker IDs, globally observed callback events, and per-mode branch-start/result ordering. A deadline fixture verifies that completed, failed, pending, and undispatched IDs form a disjoint complete ledger and that `complete` is false. Source inspection verifies explicit spawn context and no diagnostic-runner import in the pool module.

The independent review suite passed 3 tests in 2.98 seconds. The owner pool suite passed 13 tests in 14.60 seconds. The owner runner suite passed 12 tests in 0.84 seconds. The unstarted-process cleanup probe passed. Ruff and ty passed for the owned test file, and `git diff --check` passed.

All four previously reported blockers are fixed. `ModeTask` construction now supplies `candidate_branch_count` and `source_digest`. `FixedScorerFactory` declares and receives the source digest. The source-hash mismatch fixture uses paths inside the pinned checkout and reaches the intended hash comparison. The pool reap path skips unstarted processes before calling `is_alive`, so partial startup cleanup no longer masks the original failure.

The pool reuses one scorer per spawned worker, preserves per-worker branch ordering while allowing global mode interleaving, reports worker provenance, and retains exact serial-equivalent outcomes in the independent fixture. The runner keeps the 1800 second timeout, 40 GiB reserve, 50 MiB output cap, canonical lock, fixed four-worker count, and explicit incomplete-run state. No production-scale diagnostic, coast data, or GPU execution was performed.

Launch recommendation. PASS the read-only preflight and permit the planned CPU diagnostic launch. This recommendation is based on the committed source review and 28 focused tests; the diagnostic result itself remains unobserved until the authorized launch.
