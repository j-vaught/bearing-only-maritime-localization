# A30 persistent CPU candidate evaluator

## Implementation

Added `bearing_localization.search.parallel_candidates` with public `CandidatePositionInput`, `CandidatePositionResult`, `CandidatePoolTimings`, `CandidatePoolError`, and `PersistentCandidatePool` records. `iter_results` evaluates each supplied record once and returns records in original sequence order. It preserves each input coordinate and candidate ID and does not deduplicate. `score` is the serial-compatible path. It rounds candidate positions to nine decimal places, deduplicates by that position key, enforces unique IDs, applies the candidate-limit exhaustion rule, retains every candidate outcome category, sorts hypotheses stably by score, and preserves certificate and budget fields.

Workers use the spawn start method and construct a private `VectorMap` from read-only WKB payloads once at startup. Each worker constructs a `CandidateScorer` from each request's measurements and configuration, then releases the previous scorer before constructing the next. There is no truth or candidate-generation input. A bounded task queue and bounded in-flight semaphore prevent unbounded batch accumulation. Batch IDs and sequence IDs make output merging deterministic despite uneven worker completion times.

Candidate evaluation and scorer-setup exceptions return explicit per-input failures. The parent raises `CandidatePoolError` with completed, failed, outstanding, and failed candidate IDs; incomplete results are not returned as a successful run and there is no serial fallback. Worker RSS is sampled after startup and with batch results. Admission uses a serialized static/request memory estimate and checks measured aggregate worker RSS plus the estimated in-flight increment against the caller's budget. The estimate is a planning estimate, not a hard operating-system memory cap. `last_timings` separates startup, scorer setup, IPC, worker evaluation, elapsed time, counts, and sampled RSS.

## Verification

`UV_CACHE_DIR=/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/cache/uv uv sync --locked --all-groups --extra gpu` completed under Python 3.11.15. `uv run ruff format bearing_localization/search/parallel_candidates.py tests/accuracy/test_a30_cpu.py`, scoped `uv run ruff check ... --fix`, repository `uv run ty check`, `uv run pytest tests/accuracy/test_a30_cpu.py -q`, and `git diff --check` passed. The CPU test file reports 12 passed.

Tests compare serial and pooled hypotheses, statuses, budgets, and outcome counts for one, two, and four workers with type use enabled and disabled. Fixtures cover clear water, land, unknown coverage, outside coverage, unsupported feature metadata, heterogeneous bearing uncertainty, negative likelihood costs, repeated candidate positions, stable ordering, uneven batches, and candidate-budget exhaustion. The record iterator test confirms duplicates are both evaluated and preserve IDs. A spawned-worker test injects an invalid scorer configuration and verifies that the candidate-specific error is surfaced. Worker RSS is nonzero for all tested worker counts, and a 50 MB budget is rejected when actual startup RSS exceeds it.

No performance measurement or speedup claim was made. GPU devices and the A43 benchmark lock were not used because this component is CPU-only and the work was correctness-focused.

## Limits

The component uses existing `CandidateScorer` evaluation and therefore preserves its geometry, line-of-sight, assignment, and likelihood semantics. The pool is sequential at the request level; overlapping requests are rejected. The caller chooses worker count and memory budget. Resident-set sampling is Linux `/proc` based, matching the assigned server. The serialized-size estimate cannot bound Python, GEOS, allocator, or process-runtime overhead; the additional RSS check detects observed overshoot and stops with an explicit failure.
