# Eastern Seaboard GPU candidate-scoring benchmark

This experiment measures the GPU bearing-score stage across a rectangular candidate domain spanning the U.S. Atlantic coast from Florida through Maine. The bounding box is 24.5°–45.5° N and 82°–66° W. It uses 2,268 AIS positions selected from NOAA's 2023-07-15 12:00–12:01 UTC nationwide archive, plus the 427 committed Chesapeake ENC aid positions. The same seeded B1 simulator run supplies 23 global-bearing reports.

| Candidate spacing | Candidate positions | Catalog objects | Median score time | Range across three runs |
|---:|---:|---:|---:|---:|
| 10 km | 33,901 | 2,695 | 163 ms | 160–169 ms |
| 5 km | 135,359 | 2,695 | 592 ms | 577–604 ms |
| 2 km | 846,300 | 2,695 | 4,478 ms | 4,239–4,483 ms |
| 1 km | 3,383,957 | 2,695 | 18,346 ms | 18,156–18,392 ms |

Each candidate is scored against every catalog point using spherical bearings, a 10 km per-report range gate, type compatibility, and a 12° unmatched penalty. The catalog and bearings remain on the GPU between runs. Timings include uploading the candidate grid, scoring all candidates in batches of 512, synchronizing, and copying the score vector back. Each grid spacing was run three times on `comech-2080`, using one shared NVIDIA RTX 6000 Ada GPU.

The 10 km grid took 163 ms for this scoring stage, already above the 100 ms update budget. The 1 km grid took 18.3 s. This is an exhaustive rectangular-grid stress test, not the complete pairwise estimator. Candidate generation, land/water filtering, line-of-sight checks, one-to-one association, and pairwise hypothesis generation are outside these timings. The grid includes land cells, and ENC chart aids currently cover Chesapeake only. Thus the test measures full-region AIS catalog scaling but not a navigation-ready whole-coast search.

The benchmark checked GPU/CPU score agreement on a deterministic 128-candidate subset. The best candidate agreed; the maximum score difference was 0.011°. The source script is [`tools/benchmark_eastern_seaboard_gpu.py`](../../tools/benchmark_eastern_seaboard_gpu.py), and raw repeated timings are in [`scaling.csv`](scaling.csv). The AIS catalog and extraction manifest are in [`data/ais/`](../../data/ais/).

Reproduce on a machine with CUDA-enabled PyTorch and the project dependencies:

```bash
CUDA_VISIBLE_DEVICES=3 python3 tools/benchmark_eastern_seaboard_gpu.py \
  --device cuda:0 \
  --spacings-m 10000 5000 2000 1000 \
  --repeats 3 \
  --batch-size 512 \
  --out results/eastern-seaboard-gpu/scaling.csv
```
