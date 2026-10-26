# A13 Proposal Cache

The implementation and tests were committed as `d539f0147c2dc28210848fe1f995f768ba9b1ed1` on the pushed branch `accuracy/a13-proposal-cache`, based on `cc88d2a`.

## API and context allowlist

`ProposalCacheContext(measurement_sha256, catalog_sha256, map_sha256, search_config_sha256, implementation_sha256)` is an immutable caller-supplied manifest. Every field must be a lowercase SHA-256 digest. Its canonical manifest receives a deterministic context SHA-256 key. The allowlist contains no truth, reference-position, support, or evaluation fields; extra fields are rejected.

`save_proposal_cache(cache_path, result, *, context)` writes a complete cache directory. `load_proposal_cache(cache_path, *, context)` requires the same context and raises on a mismatch. A destination is write-once. The writer checks the filesystem's free space against a conservative size estimate and preserves at least 40 GiB. It writes payload and checksummed JSON to a temporary sibling directory, syncs both files and directories, then atomically renames the complete directory into place.

## Encoding and validation

Numeric NPZ arrays use only fixed-width int64 and float64 types and load with `allow_pickle=False`. Candidate order, cells, coordinates, counts, residuals, and evidence stay in arrays. Witness graphs use catalog and report indices with ragged offsets. Catalog strings, report IDs, candidate IDs, frame/configuration values, counters, and context hashes live once in strict versioned JSON. SHA-256 covers the array payload and canonical metadata body.

Loading bounds metadata, array expansion, and counts before array construction. It validates archive members and NPY headers, exact shapes and dtypes, finite floats, offset monotonicity and coverage, catalog/report bounds, unique IDs and cells, candidate order, witness uniqueness and canonical order, exact matching counts, coordinate-to-cell consistency, context, frame, configuration, and all counters before constructing the result.

A proposal result can represent `completed` and `budget_exhausted` generation. The source result type has no `failed` generation state, so a generation exception has no result to cache. The loader rejects unknown generation statuses. Failed source-pair and sample counters are preserved exactly.

The component is not wired into command-line execution. A later caller can report one-time static proposal generation and cache preparation separately, while excluding that preparation from repeated warm-search timing. No full generation or large cache was run.

## Validation

Python 3.11.15 was selected by `uv sync --locked --all-groups --python 3.11`. Ruff format, Ruff check, ty check, and `git diff --check` passed. The scoped run `uv run --python 3.11 pytest -q tests/accuracy/test_a13_proposal_cache.py tests/accuracy/test_a04.py tests/accuracy/test_a43_geodesic.py` passed all 36 tests. The cache tests include exact round trips for a seeded mock, a tiny real-generator result, empty catalog, and source-pair budget exhaustion; they also cover context mismatch, unsupported failed status, hash tampering, malformed ragged offsets, incomplete cache, numeric-only arrays, the free-space reserve, and write-once publication.

The implementation limits caches to 10 million proposals, 100 million report-witness rows, 200 million catalog-witness references, 512 MiB of metadata, and 8 GiB of uncompressed arrays. These bounds are safety limits, not tested scale claims.