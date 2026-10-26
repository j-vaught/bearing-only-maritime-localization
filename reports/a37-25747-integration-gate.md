# A37 integration gate at 25747d6

The implementation baseline was `25747d6eb01e2b336c3e198b94accf932e6920dd`. Validation after the A29 process-polling regression repair and the UTF-8 report repair ran at `c8d961cdf73c99b15941180d9c1f5de4b206aa71`. The integration branch was not modified. The A37 branch contains the A29 test-only repair and a cherry-pick of the report encoding repair.

## Results

`uv sync --locked --all-groups` passed with Python 3.11.15 using the shared UV cache. Full-project `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check .`, and `git diff --check` passed. The repository-wide formatter check initially failed on invalid UTF-8 in `reports/a33-geodesic-seeded.md` and `reports/a35-wedge-omission.md`; the report repair in `ea929a8` corrected those files. The clean full-project formatter check then reported 285 files already formatted.

The first full CPU run at the implementation baseline produced 825 passed, 31 skipped, 7 subtests passed, and one failure in `tests/accuracy/test_a29.py::test_bounded_process_timeout_terminates_grandchild`. The test observed that `os.kill(pid, 0)` succeeded, then the grandchild exited before `/proc/<pid>/stat` was read, raising `ProcessLookupError`. Commit `a5ff872004dbce9c235d1021b4ea9719cf3e8cc1` updates the test to treat disappearance between those operations as termination and adds a deterministic regression for the race. The existing deadline and live-process failure remain in place. Five repeated focused runs each passed all 10 A29 tests. The full CPU run after the repair passed with 827 passed, 31 skipped, and 7 subtests passed in 117.46 seconds. The skips are not GPU hardware evidence.

`uv build --wheel --sdist --out-dir /tmp/a37-25747-build` passed at the 25747d6 implementation baseline. Both distributions installed in separate clean Python 3.11 environments. The five installed commands `bearing-localization`, `bearing-localization-search`, `bearing-localization-evaluate`, `bearing-simulator`, and `bearing-benchmark` each accepted `--help` from both environments.

All 21 tracked Typst sources compiled to PDF under `/tmp/a37-25747-typst`. Two architecture figures emitted an `Arial` unknown-font warning; compilation succeeded. The generated PDFs total about 2.2 MiB and remain in the temporary output directory.

The test suite exercises the integrated geodesic proposals and seeds, candidate pool lifecycle, cache contracts, strict migration, solver failure statuses, and serialization and CLI truth boundaries. This gate does not establish localization accuracy, validate held-out scenes, or validate GPU execution. Screen and bound helpers that are not wired into the current end-to-end path were not treated as operational.

## Exact commands

The environment and full checks used `uv sync --locked --all-groups`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check .`, `uv run pytest -q`, and `git diff --check`.

The process-polling repair received `uv run ruff format tests/accuracy/test_a29.py`, `uv run ruff check tests/accuracy/test_a29.py --fix`, `uv run ty check tests/accuracy/test_a29.py`, and five repetitions of `uv run pytest -q tests/accuracy/test_a29.py`.

Packaging used `uv build --wheel --sdist --out-dir /tmp/a37-25747-build`. Separate clean environments installed the wheel and source distribution, then ran `--help` for all five commands listed above. Typst compilation used `typst compile --root . <tracked-source.typ> <temporary-output.pdf>` for each tracked `.typ` file.
