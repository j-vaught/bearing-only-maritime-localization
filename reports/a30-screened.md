# A30 parallel finite-candidate screened chunks

This report covers the CPU parallel chunk component in branch `accuracy/a30-screened`, based on integration commit `0085b8c79ec7eed453e0483032bbf1ac844ca564`. The component uses A22's `CandidatePosition` and `screened_candidate_search` API without modifying A22 source, the controller, the command line interface, or the earlier exact-scoring pool.

## Interface

`ParallelScreenedCandidatePool(catalog, vector_map, *, workers, memory_budget_bytes, chunk_size=32, max_in_flight_chunks=None, startup_timeout_s=30.0, request_timeout_s=None)` starts persistent spawn workers. `iter_chunks(measurements, config, candidates, *, mode="screened", complete_candidate_count=None)` accepts a finite stream of stable `CandidatePosition` records and yields compact `ScreenedChunkResult` records in input-chunk order. Callers can checkpoint each yielded chunk. The `exact` mode evaluates each candidate with `CandidateScorer.evaluate`; `screened` delegates each whole chunk to A22's screened search.

Workers retain one catalog and reconstructed map. They replace their scorer on a new request, so measurements and configuration are request-local. An incumbent is sent as an ID and position only. The receiving A22 search exact-scores that position as the first point in its chunk, then uses the resulting exact hypothesis to screen the remaining candidates. Scores are never accepted from the caller or another worker. Dispatch is bounded by the in-flight window, responses are emitted in stable chunk order, and an interrupted or failed request terminates and reaps every worker and invalidates the pool.

Each audit contains candidate ID, input index, projected position, action, exact score when evaluated, lower bound and support status when screened, visible-group count K, and model status. Full LOS evidence is not transferred. Chunk results expose exact, invalid-water, unresolved, pruned, incumbent-seed, and incumbent-validation counts, plus setup, IPC, evaluation, memory-estimate, and worker-RSS diagnostics. The serialized-size estimate is an admission estimate, not a hard memory bound. The final chunk marks request completion only after the source ends and the full input ledger is accounted. It marks finite-set completion only when every input has an audit and no unresolved candidate remains. No continuous-search certificate or accepted fix is produced.

## Verification

The command `uv run ruff format bearing_localization/search/parallel_screened.py tests/accuracy/test_a30_screened.py` completed successfully. The scoped `ruff check --fix` and `ty check` commands passed. `pytest tests/accuracy/test_a30_screened.py tests/accuracy/test_a22_screened.py -q` passed 24 tests in 8.44 seconds. The new tests exercised real spawn workers at 1, 2, and 4 processes, exact and screened modes, land and unknown outcomes, stable candidate accounting, deterministic repeated incumbent scheduling, stale-safe incumbent snapshots, winner and tie equivalence against exact scoring, empty streams, normal and abnormal worker exits, and early generator cancellation with pool invalidation. `git diff --check` passed.

No benchmark or speed claim was made. A14 observed a long tail in scalar visibility screening, so this implementation exposes exact and screened component modes for future comparison. End-to-end frame time still includes proposal generation, transfers, refinement, and serialization. No dataset, held-out input, truth value, or GPU timing was used.

## Limits

The wrapper handles a declared finite candidate stream only. Its audit history is linear in candidates when retained by the caller, while the internal task queue and outstanding chunks are bounded. A worker failure or deadline raises `ParallelScreenedError` with completed, failed, and outstanding counts; partial output remains incomplete. Empty candidate input produces no chunk and no finite-set certificate. Current correctness evidence is limited to the small A22 fixture and CPU workers 1, 2, and 4.
