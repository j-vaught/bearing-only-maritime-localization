# Parallel assignment expansion

## Implementation

Added `bearing_localization.search.parallel_assignment_expansion` with public API `run_expansion_modes(tasks, scorer_factory, *, worker_count=1, progress_callback=None, result_callback=None, deadline_s=None)`. The pool accepts one or four spawned CPU workers. A top-level picklable scorer factory exposes a common `source_digest` and constructs one worker-local `CandidateScorer` from the production inputs. Each `ModeTask` carries `mode_index`, `seed`, `config`, precomputed `candidate_branch_count`, live `package_commit`, per-mode `input_digest`, and common `source_digest`.

The pool dispatches tasks in caller order with no more than `2 * worker_count` modes in flight. This allows the runner to submit descending branch-count order while final outcomes and dispositions remain sorted by mode index. Workers execute the unchanged `expand_assignments` algorithm and emit branch-start and branch-result events through the parent `progress_callback`, tagged with mode index and phase. Per-mode event order follows branch IDs. `result_callback` receives complete `ModeOutcome` records.

Worker startup checks the factory's static source digest and each loaded package commit before dispatch. Provenance records include worker ID and PID, loaded package commit, assignment-expansion source path and SHA-256, scorer source path and SHA-256, ready RSS, and peak sampled RSS. Deadlines cover initialization and execution. Worker exceptions, abrupt exit (including exit code zero), callback failures, and startup failures raise `ParallelExpansionError` with disjoint completed, failed, pending, and not-dispatched mode IDs. Owned processes are reaped. The assignment-expansion algorithm and acceptance semantics remain unchanged.

## Validation

Locked dependency sync passed with a worktree-local environment. Scoped Ruff format/check and `ty check` passed. `uv run --locked pytest -q tests/accuracy/test_parallel_assignment_expansion.py` passed 13 tests in 14.53 seconds. Coverage includes one/four-worker exact parity, ordered branch callbacks, caller-priority dispatch with mode-index merge, budget exhaustion, startup and mode errors, exit-zero/nonzero workers, deadline cancellation, callback exceptions, source-pin mismatch, and a second-process start failure with reaping of the first child. `git diff --check` passed.

No large real-data run or timing claim was made. The serial diagnostic remains the authority for benchmark results.