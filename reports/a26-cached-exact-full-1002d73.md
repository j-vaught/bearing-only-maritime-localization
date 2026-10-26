# A26 cached exact full-domain attempt

**Purpose.** This development diagnostic exact-scores a deterministic finite set of cached full-coast proposals. Search received the frozen measurements, catalog, map, configuration, and proposal cache. It did not receive truth. The attempt evaluates feasibility and durable continuation behavior. It does not establish an accuracy result or an acceptance decision.

**Pinned inputs and software.** The controller source was commit `1002d733b557aa94c0a7da46dc6733c654f44af3`. Proposal generation was pinned to `33fded7641a0dd93c04dd2d26f290dd99dccb90d`, and candidate scoring to `0d34a17ed384da63936de0017937b38609921239`. The frozen input hashes were measurements `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1`, catalog `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`, map `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`, and scope `d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1`. The cache tree SHA-256 was `d6154c18349842d22ca1cd5eaf2d022a6ac67004eebc9e35c5c59d03ad767e72`, containing 1,457,546 candidate IDs. The run identity SHA-256 was `3402e8cbb30e690816935541ada1472ed55a0d9ec3ea5220585bdf8e6d87a569`.

The frozen setting used exact scoring, eight workers, 32-candidate chunks, a declared 64 GiB memory budget, and an 1,800-second whole-request deadline. Numerical library thread counts were set to one. The plan and inventory were reused as hardlinks from the successful A37 process smoke, with matching inodes and hashes. The exact invocation was:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run python tools/run_a26_cached_finite_domain.py \
  --generation-root .agent-work/worktrees/a26-cachegen-33fded7 \
  --scoring-root .agent-work/worktrees/a26-scoring-0d34a17 \
  --expected-generation-source-sha 33fded7641a0dd93c04dd2d26f290dd99dccb90d \
  --expected-scoring-source-sha 0d34a17ed384da63936de0017937b38609921239 \
  --static-root .agent-work/outputs/a26-screened-cache-diagnostic-v4/static-inputs \
  --cache-path .agent-work/outputs/a26-screened-cache-diagnostic-v4/cache/proposals-v1 \
  --generation-run-root .agent-work/outputs/a26-screened-cache-diagnostic-v4/cache-build \
  --output-root .agent-work/outputs/a26-cached-exact-full-1002d73 \
  --workers 8 --memory-budget-bytes 68719476736 --chunk-size 32 \
  --timeout-s 1800 --execution-mode exact \
  --reuse-plan-from .agent-work/outputs/a37-prefix-smoke-1ef881b
```

**Observed outcome.** The durable attempt ran for 859.9919 seconds before a disk-reserve intervention. It committed 1,477 chunks and 47,264 candidates. Of these, 47,182 were exact-scored, 82 were unresolved, and none were classified as failed. The complete-domain ledger lists 1,410,282 candidates as dispatched but uncommitted or otherwise unknown at interruption. The checkpoint and supervisor manifest preserve the incomplete population. Durable score records show a best-so-far objective of `14.6053352926` for candidate `cell:cell:-419:1405` at input index 46,031. This objective is a search diagnostic only and is not an accepted fix.

The sampled checkpoint reported about 1.06–1.07 GiB resident per worker. Pool telemetry recorded 859.99 seconds of full request time. The run used `uv run` without an explicit project-local `UV_CACHE_DIR` and without `--locked`; this did not prevent execution, but the next continuation should use the project cache and locked environment invocation.

**Operator stop record.** At the stop decision, `df -B1` reported 43,282,800,640 free bytes, only 333,127,680 bytes above the 40 GiB reserve. The remaining margin was below the estimated 195 MiB needed to write the final disposition ledger. At approximately 2026-10-26 11:15 UTC, `SIGTERM` was sent to worker process group `2087862` using `kill -TERM -- -2087862`. The process group exited in two seconds. The outer supervisor remained alive and wrote the complete candidate-disposition ledger, checkpoint, and supervisor manifest before returning a worker-exit error with return code `-15`. This is a deliberate disk-resource stop, not an algorithm failure or the configured search timeout. The supervisor manifest retains `status: worker_failed` as emitted by the runner; it was not rewritten after the run. Free space immediately after ledger publication was 43,072,282,624 bytes, approximately 40.11 GiB.

**Evidence and continuation.** The full immutable attempt remains under `.agent-work/outputs/a26-cached-exact-full-1002d73`. The small tracked evidence copies are under `reports/evidence/a26-cached-exact-full-1002d73`. The supervisor-manifest SHA-256 is `66df01fa92009eaf4ebcb2a6d051fa8ce726b7e8e66209e0ee9930cfb110634a`; the dispositions SHA-256 is `cdd81caa5df87dc3a7300880d833c7d200d5a5bc7c137033db660d3e5e251268`. The supervisor manifest also binds the plan, inventory, chunk-results, hypothesis, run-identity, and disposition hashes. The raw outputs remain unchanged.

A continuation must wait until disk space is increased enough to preserve the 40 GiB reserve and write its disposition ledger. It should use the same frozen generation and scoring identities, verify the existing attempt and cache lineage, set a project-local UV cache, and invoke the locked environment. No truth, held-out records, or changed candidate budgets were used in this attempt.
