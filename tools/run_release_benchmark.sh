#!/usr/bin/env sh
set -eu

uv run bearing-localization \
  --trials 10 \
  --seed 20261023 \
  --ranges-km 10,5,1 \
  --features 3,4,6,8,12 \
  --object-count 350 \
  --scene-km 40 \
  --ais-fraction 0.7 \
  --noise-deg 0.5 \
  --grid-km 1 \
  --refine-starts 8 \
  --target-success-rate 0.95 \
  --backend-candidates 512 \
  --backend-repeats 5 \
  --output-dir results/synthetic-baseline-v0.1.0

uv run python tools/export_failure_case.py

typst compile --format png figures/range-constraint.typ docs/figures/range-constraint.png
typst compile --format png figures/benchmark-performance.typ docs/figures/benchmark-performance.png
typst compile --format png figures/failure-case.typ docs/figures/failure-case.png
