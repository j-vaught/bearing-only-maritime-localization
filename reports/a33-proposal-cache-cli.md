# A33 Proposal Cache CLI Wiring

## Interface

The strict `SearchConfig` JSON accepts an optional `proposal_cache_path` string. Its default is `null`, preserving existing behavior. The field is supported only by `wedge_geodesic` and `wedge_geodesic_seeded`. Relative paths resolve from the process working directory. The destination is a cache directory and needs no suffix.

A missing destination triggers full geodesic proposal generation, then the A13 atomic cache writer publishes the complete result. A pre-existing destination is loaded strictly. Context mismatch, malformed metadata, missing files, hash errors, and invalid caches stop the request; the CLI does not regenerate over an existing path. The cache writer enforces its 40 GiB free-space reserve. A failed cache save also stops the request.

## Cache identity

The five-field manifest is `measurement_sha256`, `catalog_sha256`, `map_sha256`, `search_config_sha256`, and `implementation_sha256`. Input hashes come from the exact byte strings parsed for the current request or its validated session snapshot.

The generation configuration digest binds algorithm, map projection and frame, declared bounds, maximum and minimum range, type-use flag, generated and floor sigma, every effective per-report sigma, wedge cell size and angular tolerance, range and angular sampling policies, effective and configured source-pair budgets, cell budget, source chunk size, and solver tolerance. The implementation digest hashes the geodesic generator, its input record definitions, local-frame and reverse-bearing implementations, and the locked dependency manifest. It excludes downstream ranking and scoring code. Candidate scoring budget, seed-generation budget, worker count, and the cache location are excluded, allowing those downstream controls to change while reusing the exact pool.

Cached proposal order, candidate IDs and cells, coordinates, witness graphs, proposal counters, numerical-failure counters, and generation budget status are loaded from the A13 result schema without reconstruction. Incomplete source-pair or cell generation stays budget-exhausted. Failed numerical samples remain failed and cannot establish complete generation. The seeded algorithm uses the same cached proposal pool before its request-local seed generation.

## Telemetry

The sidecar `diagnostics.proposal_cache` records whether caching is enabled, whether the cache was hit, the action, cache path, exact generation settings, all five hashes, context hash, and separate generation/load/save wall times. Stage telemetry records `proposal_generation`, `proposal_cache_load`, and `proposal_cache_save` independently. Existing complete-request timing remains the authority for request cost through serialized output; `LocalizationResult.elapsed_s` retains its prior scoring/refinement meaning.

No truth, support, or evaluator data is read or written by this path.

## Validation

Scoped command results on branch `accuracy/a33-proposal-cache` from base `25747d6eb01e2b336c3e198b94accf932e6920dd`.

- `uv run ruff format bearing_localization/accuracy_cli.py bearing_localization/search/config.py tests/accuracy/test_a33_proposal_cache_cli.py` passed.
- `uv run ruff check bearing_localization/accuracy_cli.py bearing_localization/search/config.py tests/accuracy/test_a33_proposal_cache_cli.py --fix` passed.
- `uv run ty check bearing_localization/accuracy_cli.py bearing_localization/search/config.py tests/accuracy/test_a33_proposal_cache_cli.py` passed.
- `uv run pytest -q tests/accuracy/test_a03.py tests/accuracy/test_a13_proposal_cache.py tests/accuracy/test_a33.py tests/accuracy/test_a43_geodesic.py tests/accuracy/test_a30.py tests/accuracy/test_a33_proposal_cache_cli.py` passed with 94 passed and 11 expected hardware skips, including destination-preflight repair.
- `git diff --check` passed.

The new tests cover fresh generation and exact same-budget warm-pool output parity, changed downstream candidate budget reuse, measurement/config/implementation context mismatches, corrupt-cache rejection without output or regeneration, absent sigma with generated default and sigma floor, source-budget exhaustion roundtrip, and numerical-failure counter roundtrip. No large or frozen-input generation was run.


## Cache destination preflight repair

The CLI now creates missing cache parent directories and probes create/delete access before proposal generation. Existing paths remain strict-load targets and are never overwritten. A file in the parent chain, directory creation error, or failed writable-directory probe stops before the generator runs. The CLI also verifies that a generator exception leaves no claimed cache and that cache publication errors are reported distinctly after generation. The tests also verify that a generator exception can leave the prepared parent directory without producing a cache or estimate, and that a later cache publication failure is reported as a cache-save failure after generation rather than as an incomplete-generation status.

The cache destination preflight repair is committed separately after the initial wiring milestone. It does not change the proposal-generation implementation digest or hash inputs.
