# A37 regression gate at `07d8036`

This report records the A37 release-correctness regression gate for integration commit `07d8036164c6d2db238f38e51f43ba6c1bd93676` in the isolated A37 worktree on `comech-2080`. The worktree was clean before and after verification. A26 released the exclusive diagnostic lock before any environment setup or computation. The A26 output directory was left untouched.

## Scope

This gate exercised the locked Python 3.11 CPU environment, architecture and serialization boundaries, the full test suite, source formatting and static checks, package distributions and installed entry points, and every tracked Typst source. No A37 source or CI files were changed, so no new source commit was required. This is not an accuracy claim or a validation of complete-coast coverage.

## Results

`uv sync --locked --all-groups --python 3.11` passed with the shared UV cache. CuPy was absent from the resulting default environment.

`uv run ruff format --check .` passed with 235 files already formatted. `uv run ruff check .`, `uv run ty check .`, and `git diff --check` passed.

The full `uv run pytest -q` suite passed with 709 passed, 28 skipped, and 7 subtests in 109.03 seconds. Skips are not GPU hardware validation. The explicit truth-removal and architecture regression command `uv run pytest -q tests/accuracy/test_a37.py tests/test_cli_smoke.py tests/test_architecture_boundaries.py` passed all 13 tests in 3.63 seconds. The CLI smoke test generates measurements, AIS, map, and truth files, deletes the truth file, and then invokes standalone search using only estimator inputs. The architecture tests check static and runtime import restrictions, dynamic import escape hatches, and truth-free contract fields.

`uv build --out-dir /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/build/a37-07d8036` built the wheel and source distribution. Each artifact was installed into a separate clean Python 3.11 environment. Imports for the installed package namespaces passed, and all five installed command entry points passed `--help`: `bearing-localization`, `bearing-localization-search`, `bearing-localization-evaluate`, `bearing-simulator`, and `bearing-benchmark`.

All 19 tracked Typst sources compiled successfully with `typst compile --root .` into `/tmp/a37-07d8036-typst`. No source failed to compile. The architecture figures `shared-library-architecture.typ` and `software-architecture.typ` emitted the existing unknown Arial font-family warning; compilation succeeded for both.

## Limits

The CPU-only skipped tests provide no GPU hardware evidence. This A37 gate tests release correctness only. The source-level coast map remains incomplete with explicit unknown coverage regions, so these results do not support complete-coast accuracy claims. No held-out evaluation or accuracy metric was run by A37.
