# Eastern Seaboard GPU search benchmark

## Scope

This benchmark tested candidate scoring over the full East Coast search rectangle, from Florida through Maine and nearby waters. It used the seeded 23-report B1 Chesapeake case, 2,268 AIS vessel positions, and 51,416 deduplicated NOAA ENC chart aids, for 53,684 catalog locations. Every candidate was range-gated to 10 km.

The benchmark uses one NVIDIA RTX 6000 Ada (physical GPU 0) on comech-2080. The host is shared; the other three GPUs had active jobs at the time, so this run did not distribute work across cards. The GPU was not otherwise under compute load during the measurement. Timings are one-pass measurements rather than a controlled multi-run study.

## Search timing

| Search grid | Candidates | CPU score | GPU score plus transfer | GPU neighborhood query plus score | Score-only speedup |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 km | 360 | 45 ms | 4.4 ms | 5.2 ms | 10.3x |
| 10 km | 33,135 | 2,869 ms | 114 ms | 171 ms | 25.1x |
| 5 km | 131,508 | 8,930 ms | 436 ms | 665 ms | 20.5x |
| 2 km | 820,638 | 43,309 ms | 2,445 ms | 3,765 ms | 17.7x |

These CPU/GPU comparisons use the same parallelizable surrogate score. The score independently chooses the nearest compatible catalog object for each report, so one AIS object can explain more than one report. We exact-reranked the top 100 candidates with the repository's one-to-one Hungarian association to measure the surrogate's ranking quality. Grid construction and exact reranking are excluded from the timing columns. Neighborhood generation is included in the combined GPU timing.

The kernel is substantially faster on the GPU, but this does not meet the eventual 10 FPS target for exhaustive 10 km or finer grids. The GPU scoring stage alone takes 114 ms at 10 km spacing; including neighborhood queries raises that to 171 ms. At 2 km spacing, the combined measured stage takes 3.77 seconds.

## Accuracy and algorithm behavior

The GPU coarse-to-fine search evaluated 543 candidates in 20 ms and estimated B1 at 28.0 km error using the surrogate score. The previous coast-wide CPU hierarchical run used exact one-to-one association, took 395 ms, and estimated 4.08 km error. They are different objectives, so this is not an apples-to-apples accuracy comparison. It demonstrates that the fast surrogate can select a different basin and needs an exact association model inside the search loop or a stronger pruning/ranking strategy.

For the exhaustive grid, CPU and GPU selected the same surrogate winner at all tested spacings. Their maximum per-candidate score difference grew to 0.60 normalized score units on the 2 km run. For the 2 km type-aware case, exact reranking of the surrogate's top 100 candidates found a 7.97 km error. The 10 km and 5 km top-100 reranks remained much farther from the observer. Thus the GPU kernel's numerical agreement does not establish that the surrogate objective localizes reliably.

## Limits and next experiment

The rectangle contains land candidates, and the estimator does not apply a coast-wide water mask or land-blocked line of sight. The measurements come from a Chesapeake simulator scene while the search catalog spans the East Coast. No tested result is a coast-wide operational fix. The report evaluates GPU scoring feasibility, not the final localization algorithm.

The next useful GPU implementation is a batched exact-association hybrid. The GPU can generate per-candidate bearing costs, then a one-to-one assignment stage must preserve the exact objective before beam selection. Candidate-level land filtering and ray visibility must also be added before interpreting full-region search performance.

## Reproduction

The source is `tools/benchmark_eastern_seaboard_gpu_search.py`. The raw rows are in `gpu-search.csv`, and the large-format figure source is `../../figures/eastern-seaboard-gpu-search.typ`.

On comech-2080 with the existing CUDA-enabled PyTorch installation:

```sh
CUDA_VISIBLE_DEVICES=0 python3 tools/benchmark_eastern_seaboard_gpu_search.py \
  --device cuda:0 \
  --spacings-km 100 10 5 2 \
  --out results/eastern-seaboard-gpu-search/gpu-search.csv
```

The benchmark imports the East Coast case builder from `tools/benchmark_eastern_seaboard_search.py`. Input sources and checksums are documented in `results/eastern-seaboard-search/config.json`.
