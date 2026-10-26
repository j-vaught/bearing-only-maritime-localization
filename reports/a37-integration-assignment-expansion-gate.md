# A37 frozen integration CPU regression gate

Author: J.C. Vaught.

## Source and environment

The isolated worktree was pinned to `03d34590afcac28cc7961d9b560b17aa39284bc3` at `.agent-work/worktrees/a37-integration-918ab7b`. This was the existing isolated A37 worktree, detached from the frozen integration commit; the root checkout and root environment were not changed. The existing CPU environment at `.agent-work/envs/a37-driver-smoke` and installed dependencies were reused. The lockfile hash was `b51fff7ca48f5b331b9cfcb6face02fea64dd7f3`. No dependency download occurred.

## Commands and results

The full suite command was:

```text
PYTHONPATH=$WT $ENV/bin/python -m pytest -q
```

It completed with **1,152 passed, 32 skipped, 7 subtests passed, and 8 failed**.

Two failures were the parameterized standalone CLI tests in `test_a26_assignment_expansion.py`. They call `bearing-localization-search` via subprocess, but the invocation did not add the reused environment's `bin` directory to `PATH`. Treat those two as environment-caused, not product evidence.

The six remaining failures were:

- `test_fresh_and_loaded_proposal_cache_preserve_pool_and_allow_scoring_budget_change`.
- `test_cache_context_mismatch_and_corruption_fail_without_regeneration`.
- `test_generation_config_or_source_change_rejects_existing_cache`.
- `test_missing_sigma_uses_generated_default_and_floor_and_remains_in_pool`.
- `test_source_pair_budget_and_failed_numerical_samples_survive_cache`.
- `test_missing_cache_parent_is_created_before_generation`.

The last failure surfaced `RuntimeError: Proposal cache save failed after proposal generation` for a nested cache path. The lower-level cause was not available in the captured output. No targeted rerun was performed because free space was at the storage floor.

`ruff format --check .` passed with 392 files already formatted. `ruff check .` passed. Direct invocation of the environment's ty binary lacked environment discovery and emitted unresolved dependency imports; `uv run --no-sync ty check .` passed.

Installed CLI help checks passed with the environment `bin` on `PATH` and the frozen worktree on `PYTHONPATH`: `bearing-localization`, `bearing-localization-search`, `bearing-localization-evaluate`, `bearing-simulator`, `bearing-benchmark`, and `tools/run_a26_cached_finite_domain.py`.

## Storage and scope

No wheel or source distribution was built or installed. No GPU or timing jobs were run. The full pytest run created `/tmp/pytest-of-jvaught2/pytest-984`; this was the owned temporary tree from this gate and was removed after the run, restoring 42.96 GB free. No other output was removed.

The 32 skips do not establish hardware behavior. This CPU regression gate is not evidence of localization accuracy, full-domain scoring, complete-coast coverage, or held-out performance. No production source was changed.
