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


## Corrected targeted rerun

The source remained pinned to `03d34590afcac28cc7961d9b560b17aa39284bc3`. The eight tests above were rerun together using the same CPU environment with `PATH=$ENV/bin:$PATH`, `PYTHONPATH=$WT`, and `--basetemp=$ROOT/.agent-work/tmp/a37-03d3459-failed-tests-20261026/pytest`. Result: **8 passed in 5.04 seconds**. The exact short pytest output is committed in `reports/a37-integration-assignment-expansion-rerun.log`.

The two standalone tests had failed previously because their subprocess could not find `bearing-localization-search` with the original PATH. All six proposal-cache tests passed in the corrected rerun. The earlier full run was performed at the storage safety floor; after deleting only its owned 1.7 GiB pytest temporary tree, free space returned above the floor before this rerun. This supports classifying those six failures as environment reserve pressure rather than source regressions; the full-trace output for the prior run had shown the cache save error but was truncated before its lower-level cause. No code or test was changed between runs.



## Integration gate at `603240cb5cab8b0a21fb29208a827ff771be161d`

The existing isolated A37 worktree was pinned to the clean source commit `603240cb5cab8b0a21fb29208a827ff771be161d`; the root checkout and CPU environment were not modified. The lockfile remained at SHA-256 `b51fff7ca48f5b331b9cfcb6face02fea64dd7f3`. The same CPU environment was used without sync or downloads.

The targeted command set `PATH=$ENV/bin:$PATH` and `PYTHONPATH=$WT`, then ran the A26 core, A26 assignment expansion and cached-domain driver, A32, A33 cache CLI, A35 assignment expansion/evaluation/diagnostics, standalone and parallel expansion, evaluation contracts/metrics/CLI, and search contract tests. It completed with **165 passed and 1 skipped in 75.57 seconds**. The passing targeted cases include assignment-expansion CLI and metadata evaluator regressions.

Ruff format check passed with 393 files already formatted. Ruff check passed. `uv run --no-sync ty check .` passed. Help checks passed for all five installed CLI commands and the cached-domain driver. The source worktree remained clean at the pinned SHA.

Hosted CI run 196 (run ID `40339336620`) was still in progress when inspected. Its Typst figure build job had completed successfully; its Python job had completed sync and CPU-only environment checks, but its full `pytest` step was still in progress. The explicit GPU job was skipped. Hosted CI therefore does not yet provide a completed full-suite result.

No wheel or source-distribution build was run locally because the hosted CI job covers those steps and disk space was near the project reserve. The 176 MiB pytest temporary directory from this targeted run was removed after capturing the results. No large outputs, datasets, held-out runs, or production changes were created by this gate.
