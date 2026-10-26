# Parallel assignment expansion diagnostic

**Scope and status**

The runner uses the completed A20 and assigned-mode artifacts through the same source-pin checks, mode-reference validation, and deterministic best-of-two start selection as the serial diagnostic. It does not read truth or evaluator outputs. Before dispatch, the coordinator canonically revalidates both saved starts for all 64 modes, exactly preserving the serial input selection, and releases its scorer before creating worker processes. Four persistent spawned CPU workers each build one scorer and process bounded whole-mode jobs.

Each mode retains the fixed 1,000 m local radius, 100 function-evaluation cap, and 5,000-branch cap. The runner precomputes each mode’s candidate count from the existing candidate-pool helper, then dispatches modes in descending count order with mode index as the tie-break. This changes job priority only. All 64 modes remain in the run. Results and final row files return to mode-index order, while the ranked list uses objective score with mode index as the tie-break.

**Durability and provenance**

Branch start and result events are appended by the coordinator to one fsynced, globally chained event stream. Each record also has a mode-local sequence number, stable mode index, and the original branch event identifier. Completion rows are atomically rewritten in mode-index order whenever a mode finishes. Checkpoints distinguish completed, failed, and unknown mode indices, and retain pending and not-dispatched pool dispositions. Resume is disabled. A worker failure, timeout, callback error, output cap, or incomplete inventory cannot produce a completed run.

The manifest records source pins, the loaded package commit and assignment-expansion file hash from the coordinator and each worker, each worker’s scorer source path and hash, process IDs, ready and peak resident memory, validation/setup elapsed time, and the count-based dispatch order. The output directory is fixed at `.agent-work/outputs/parallel-assignment-expansion-diagnostic-v1`; the runner enforces the shared canonical benchmark lock, the 40 GiB free-space reserve, a 50 MiB output cap, a 1,800 s maximum deadline, and one numerical thread per worker.

The result always leaves `decision_status` as `ambiguous`, `search_status` as `budget_exhausted`, `certificate_scope` as `none`, and `full_domain_complete` as false. It does not claim that bounded local expansion proves a global optimum. The manifest separately reports preflight, visited, and unvisited branch counts and requires complete inventory agreement before marking all 64 modes complete.

**Validation**

The focused tests cover standalone import and numeric-thread setup, parity of selected starts and stable mode IDs, descending-count dispatch priority, source-hash rejection, out-of-order event interleaving and chained indices, durable partial rows and unknown-mode counts, canonical-lock resolution, current loaded-package commit and source hashes, and branch-inventory arithmetic. The combined pool and runner suites pass 25 tests. Ruff, `ty`, and `git diff --check` pass on the owned files. The diagnostic itself has not been launched, so this report makes no runtime or speedup claim.

J.C. Vaught
