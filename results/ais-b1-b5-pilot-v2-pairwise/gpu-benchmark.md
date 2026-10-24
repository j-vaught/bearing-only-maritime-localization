# GPU scoring feasibility on comech-2080

The benchmark isolates the expensive candidate-to-object bearing score used as a building block in localization. It creates synthetic candidate positions, catalogs, bearing observations, and broad object classes, then computes the sum of each report's nearest angular residual. The CPU path uses chunked NumPy; the GPU path uses PyTorch tensors on one NVIDIA RTX 6000 Ada. Each GPU timing includes allocations, host-to-device input copies, scoring, synchronization, and copying the score vector back. CUDA context startup is warmed up before timing.

| Hypotheses × objects × reports | Type gate | NumPy CPU | GPU end to end | Speedup | Max score difference | Winning index |
|---:|:---:|---:|---:|---:|---:|:---:|
| 8,192 × 800 × 10 | Off | 577.5 ms | 1.9 ms | 302× | 0.00024° | Same |
| 8,192 × 800 × 10 | On | 622.5 ms | 15.0 ms | 42× | 0.00021° | Same |
| 32,768 × 800 × 10 | Off | 2,097.2 ms | 17.5 ms | 120× | 0.00024° | Same |
| 32,768 × 800 × 10 | On | 2,346.5 ms | 10.8 ms | 217× | 0.00018° | Same |
| 32,768 × 2,000 × 10 | Off | 5,403.8 ms | 14.1 ms | 383× | 0.00015° | Same |
| 32,768 × 2,000 × 10 | On | 6,258.4 ms | 15.7 ms | 399× | 0.00015° | Same |
| 65,536 × 800 × 10 | Off | 4,280.2 ms | 10.5 ms | 408× | 0.00024° | Same |

Across all 12 CPU/GPU cases, the lowest-scoring candidate index agreed exactly; score correlation was 1.0 and the largest absolute per-hypothesis score difference was 0.00037°. The GPU was shared during the run and reached about 82% utilization, so timings are feasibility measurements rather than a controlled performance baseline.

This result demonstrates that batched bearing scoring can use GPU parallelism. It does not include geometric pair enumeration, candidate generation, map projection, water tests, land line-of-sight, exact one-to-one association, optimizer refinement, or end-to-end input handling. The current experiment therefore does not establish 10 FPS for the complete search, much less for an Eastern Seaboard catalog.

## Reproduction

The benchmark source is `tools/benchmark_pairwise_gpu_scoring.py` and the raw cases are in `gpu-score-kernel-comech-2080.csv`. On `comech-2080`, which has four RTX 6000 Ada GPUs and PyTorch 2.9.0+cu128, the run used physical GPU 3:

```bash
git clone https://github.com/j-vaught/bearing-only-maritime-localization.git /tmp/bearing-only-maritime-localization
cd /tmp/bearing-only-maritime-localization
CUDA_VISIBLE_DEVICES=3 python3 tools/benchmark_pairwise_gpu_scoring.py \
  --device cuda:0 \
  --out results/ais-b1-b5-pilot-v2-pairwise/gpu-score-kernel-comech-2080.csv
```

The benchmark requires NumPy and PyTorch with CUDA support. The recorded run used the host's existing PyTorch installation rather than adding a GPU-specific dependency to the base Python project.

The B1–B5 geometric-search runs are separate from this synthetic kernel benchmark. Their reproducible runner is `tools/run_ais_enc_pilot.py`; it loads the fixed AIS, ENC aid, water-mask, and site inputs under `data/`.
