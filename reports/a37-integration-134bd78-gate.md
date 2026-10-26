# A37 frozen integration CPU regression gate

Author: J.C. Vaught.

## Source and environment

The isolated worktree was pinned to integration commit `134bd7817633d65f4c5d6c3e8ca925c8f177f18d` at `.agent-work/worktrees/a37-integration-134bd78`. The root checkout and A26 scoring checkout were not modified. The existing A37 CPU environment was reused at `.agent-work/envs/a37-driver-smoke`; the shared uv cache was `.agent-work/cache/uv`.

## Commands and results

`uv sync --locked --all-groups` completed successfully and installed the frozen checkout into the isolated A37 environment.

`uv run pytest -q` completed with **926 passed, 32 skipped, and 7 subtests passed** in 145.24 seconds.

`uv run ruff format --check .` passed with 321 files already formatted. `uv run ruff check .` passed. `uv run ty check .` passed.

Installed CLI help smoke checks passed for `bearing-localization`, `bearing-localization-search`, `bearing-localization-evaluate`, `bearing-simulator`, and `bearing-benchmark`. The cached-domain driver also passed `tools/run_a26_cached_finite_domain.py --help`.

## Scope and remaining checks

No production source changes were made. No wheel or source distribution was built or installed. At the time of the gate, the host had 41 GiB free, leaving only about 1 GiB above the required reserve while the separate exact run was active. I stopped before creating build artifacts that could reduce the reserve. The package build/install smoke remains outstanding. Typst figure builds were not repeated because the integration contained no figure changes; the complete figure CI gate can run after the exact run.

The observed skips are pytest skips, not hardware validation. This CPU gate does not establish GPU behavior, full-domain scoring, localization accuracy, complete-coast map coverage, or held-out performance.
