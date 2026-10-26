# A33 seeded geodesic candidate path

## Scope

Added the experimental `wedge_geodesic_seeded` search algorithm. It uses the existing geodesic wedge generator and ranking, selects a configured ranked prefix for continuous witness-ray seed generation, then exact-scores a deterministic round-robin of generated seeds across selected ranked cells before every original ranked cell center. Each cell's seed order follows A19 output order. Final candidate deduplication keeps the existing 1e-9 meter rounding rule. Seeds remain proposals and do not alter the objective, truth inputs, or acceptance evidence. Existing `wedge_geodesic` dispatch remains on its prior serial scoring path.

Search configuration adds positive integer controls `seed_cell_budget` (256), `seed_pair_budget` (100000), `seed_candidate_budget` (4096), `candidate_workers` (1), and `candidate_pool_memory_budget_bytes` (8589934592). They serialize through the existing SearchConfig contract and are included in the execution fingerprint.

Seed selection does not discard cells based on center water status. All ranked centers remain in the stream even when the exact-score budget is smaller; no centers are silently removed. Unselected seed cells, exhausted seed budgets, incomplete proposal generation, or incomplete exact scoring prevent acceptance. Failed geodesic samples, seed pairs, pool startup, or worker evaluations produce a failed result. Pool failures emit failure kind, counts, and error detail where available.

Telemetry separates proposal generation, seed generation, proposal ordering, pool startup, and exact scoring. It records the candidate stream policy, scored seed/center counts, distinct seed cells represented, remaining unique candidates, and scored candidate IDs. `LocalizationResult.elapsed_s` retains exact-scoring-plus-refinement semantics; request timing through serialized estimate output remains the complete-request authority.

## Validation

After integrating A30 lifecycle repair prerequisite `a1f3df8` (upstream repair `bc4b472fa004ecbfcb133b78851ca0334a3ec753`), the 1, 2, and 4 worker seeded fixture produced identical hypotheses, decision/search statuses, objective outcome counts, and evaluated candidate IDs. A constrained fixture has more than 400 original cell centers remaining after scoring 16 seeds; all 16 seed candidates reached the exact scorer before any center. A separate water/land classification regression verifies that a selected cell center can be land while its recoverable seed is water. A deliberately short seed-cell budget reported `budget_exhausted` with no accepted index. An injected pool-startup failure reported `failed` with no accepted index.

Commands run from the isolated A33 worktree.

- `uv run pytest -q tests/accuracy/test_a30_cpu.py tests/accuracy/test_a32.py tests/accuracy/test_a33.py tests/accuracy/test_a36.py`. 66 passed, 6 skipped.
- `uv run ruff format bearing_localization/accuracy_cli.py bearing_localization/search/config.py tests/accuracy/test_a33.py`. passed.
- `uv run ruff check bearing_localization/accuracy_cli.py bearing_localization/search/config.py tests/accuracy/test_a33.py --fix`. passed.
- `uv run ty check`. passed.
- `git diff --check`. passed.

No held-out data or performance claims were used.
