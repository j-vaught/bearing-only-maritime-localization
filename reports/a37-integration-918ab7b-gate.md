# A37 frozen integration CPU regression gate

Author: J.C. Vaught.

## Source and environment

The isolated worktree was pinned to integration commit `918ab7b2304f4a81951db18fdfec54f11a33f11d` at `.agent-work/worktrees/a37-integration-918ab7b`. The root checkout, assignment-expansion worktrees, and root environment were not modified. The existing A37 CPU environment at `.agent-work/envs/a37-driver-smoke` and shared uv cache at `.agent-work/cache/uv` were reused. No dependency download occurred. The environment sync only reinstalled the editable project from the isolated worktree.

## Commands and results

`UV_CACHE_DIR=.agent-work/cache/uv UV_PROJECT_ENVIRONMENT=.agent-work/envs/a37-driver-smoke uv sync --locked --all-groups` completed successfully.

`uv run pytest -q` completed with **1,016 passed, 32 skipped, and 7 subtests passed** in 127.43 seconds.

`uv run ruff check .` passed. `uv run ty check .` passed. `uv run ruff format --check .` failed on one file, `tests/accuracy/test_a06_pre_los_audit.py`; Ruff reports it would reformat the coverage geometry call, detection constructor, group representative and uncertainty expressions, predicted tuple append, and edge-floor assertion. The remaining 355 files were already formatted. No changes were made to this unowned test file; the integration owner is handling the mechanical format correction.

Installed CLI help checks passed for `bearing-localization`, `bearing-localization-search`, `bearing-localization-evaluate`, `bearing-simulator`, and `bearing-benchmark`. The cached-domain driver also passed `tools/run_a26_cached_finite_domain.py --help`.

## Deferred checks and scope

No wheel or source distribution was built or installed, and no external dependencies were downloaded. Free space fluctuated between 41 and 43 GiB during the gate, leaving at most about 3 GiB above the required 40 GiB reserve. The package build/install check remains outstanding. No GPU or timing jobs were run. The pytest skips do not establish hardware behavior. This CPU gate does not establish localization accuracy, full-domain scoring, complete-coast map coverage, or held-out performance. No production source was changed.
