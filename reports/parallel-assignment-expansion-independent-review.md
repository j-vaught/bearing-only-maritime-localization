# Independent review of the whole-mode CPU pool

This review covers the uncommitted whole-mode pool source in the
`accuracy/parallel-assignment-expansion` worktree and the uncommitted runner
source in the `accuracy/parallel-assignment-expansion-diagnostic` worktree.
The checked-out base for both worktrees was `cbc940d`. The producer files were
not modified by this review. The exact producer commit SHA must be recorded
after both owners commit the revised source.

The independent fixture uses four distinct per-mode input digests, a shared
package commit, a shared static source digest, and a real `CandidateScorer`
constructed from a small WGS84 vector-map fixture. It runs four spawned workers
with a 30 second local deadline and compares every mode against an independent
serial `expand_assignments` result. The comparison covers status, completion,
budget state, all counters, best hypothesis, and branch-event payloads. It
also checks four unique worker IDs, globally observed callback events, and
per-mode branch-start/result ordering. A second fixture forces a deadline
during worker startup and verifies that completed, failed, pending, and
undispatched IDs form a disjoint complete ledger and that `complete` is false.
A source inspection checks that the pool explicitly uses the spawn context and
does not import the diagnostic runner.

The three independent tests passed in 2.97 seconds. Ruff passed for the owned
test file, and ty passed with the standalone test import path configured.

The worker pool source is coherent for these tested behaviors. It creates one
scorer per spawned worker and reuses that scorer across tasks. It preserves
per-worker message order while allowing modes to interleave globally. Its
cleanup path reaps only the processes it created, and callback failures or
deadline failures become a partial result with an explicit disposition
snapshot. The current `ModeTask` contract correctly permits distinct input
digests while requiring one package commit and one static source digest.

The runner is blocked before dispatch against the current worker API. Its
`prepare_mode_tasks` call omits both required `candidate_branch_count` and
`source_digest` fields. In addition, `FixedScorerFactory` declares only
`static_dir` and `config_path`, but `run` constructs it with a third
source-digest argument, which raises `TypeError` before the pool starts. These
are deterministic source-level blockers and must be repaired before the
64-mode run.

The runner diagnostic suite independently reports one failure in
`test_worker_source_hash_mismatch_is_rejected`. Its fixture uses
`/repo/.../scoring.py`, so validation rejects the path as outside the pinned
checkout before reaching the intended hash-mismatch assertion. The test or
validator fixture needs a path inside the pinned checkout to exercise the
claimed mismatch condition. No coast, GPU, or production-scale diagnostic was
run.
