# Assignment-conditioned refinement diagnostic

This diagnostic evaluates one bounded local proposal per fixed A20 mode. It consumes the finalized A20 cached-mode artifact and uses exactly its 64 preselected rows. It does not read truth or evaluator outputs and does not select new seeds. The result is an empirical comparison over these 64 modes with certificate scope `none`; candidates outside the selected modes remain unknown.

## Execution contract

The runner requires the A20 artifact at `.agent-work/outputs/a20-cached-refinement-v2`, the immutable original attempt, the A20 static inputs, the frozen scoring configuration, and the exclusive `canonicalbenchmark.lock`. It verifies the original run identity, supervisor and candidate tree hashes, static input hashes, configuration hash, source row references, row order, and all 64 A20 row hashes before constructing the current scorer. The current package commit and frozen scoring source commit are recorded separately because this proposal module is a bounded post-source refinement.

Set `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and `NUMEXPR_NUM_THREADS` to one. Run from the repository root with the existing verified Python 3.11 environment.

```sh
cd /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/assigned-bearing-refinement
PYTHONPATH=. /home/jvaught2/projects/bearing-only-maritime-localization/.venv/bin/python tools/run_assigned_refinement_diagnostic.py \
  --a20 ../../outputs/a20-cached-refinement-v2 \
  --attempt ../../outputs/a26-cached-exact-full-1002d73 \
  --static ../../outputs/a26-screened-cache-diagnostic-v4/static-inputs \
  --config /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/a26-scoring-0d34a17/data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/search-config.json \
  --output ../../outputs/assigned-bearing-refinement-diagnostic \
  --lock ../../benchmark.lock \
  --timeout-s 1800
```

The output is bounded to 5 MiB and checks the 40 GiB free-space floor before each mode. The only accepted lock path is the repository canonical `.agent-work/benchmark.lock`, passed as `../../benchmark.lock` from this worktree. The only accepted lock path is the repository canonical `.agent-work/benchmark.lock`, passed as `../../benchmark.lock` from this worktree. The lock is exclusive for the whole run. A checkpoint is fsynced after every completed mode. An interrupted run preserves its completed rows and records `interrupted`; it is not a completed comparison.

## Per-mode procedure

Each mode has two fixed starts. The first is `cached_hypothesis`. The second is `refined_mode.hypothesis` from the immutable A20 row. Each start is re-evaluated by the current `CandidateScorer` before fitting. The score must match the frozen score with absolute tolerance $10^{-8}$ and relative tolerance $10^{-10}$, and the exact report-to-catalog assignment records must match. A mismatch stops the diagnostic rather than permitting a stale source row to influence ranking.

Each start permits at most five sequential calls to `refine_assigned_bearing`. A proposal enters the next step only when its result status is `improved`, its exact canonical hypothesis is present, and its displacement is at least $0.01$ m. Any no-improvement, unresolved, unsupported, solver, maximum-evaluation, exception, or sub-threshold proposal stops that start. Every step retains solver counts, status, proposed pose, exact hypothesis when present, score, and assignment records. The final canonical scorer performs the complete objective and any reassignment, including water and coverage handling.

The runner writes `best_hypothesis` for each mode and a `ranked_selected_modes` list containing mode index, source start, score, pose, and exact assignments. It never marks a localization fix accepted and never emits a completeness claim.

## Result schema

`manifest.json` identifies the harness and package commits, source pins, A20 manifest and rows hashes, mode count, numerical tolerances, lock and resource contract, and completion state. `assigned-refinement-rows.jsonl` contains one record per mode with immutable source references, two start records, all step records, the final hypothesis for each start, and the best per-mode hypothesis. `checkpoint.json` contains the completed prefix and its rows hash; the fsynced rows file retains the detailed mode records. The `ranked_selected_modes` list is present only when all 64 selected modes complete; its scope is the selected modes rather than the whole search domain.

The focused contract tests cover complete and partial artifacts, invalid row hashes, stale source references, boolean status masquerading as a hypothesis, real localization dataclass decoding, interrupted proposals, maximum-evaluation stops, and stale initial scores. No diagnostic execution or timing run is authorized until the benchmark lock is released by the coordinator.

## Completed run

The released run completed all 64 selected modes in 57.6525 s. The raw artifact is `/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/assigned-bearing-refinement-diagnostic-v1`. Its total size is 830,502 bytes. The manifest SHA-256 is `ecde0ac0eaab7eef8569638f306e2a8c6300a78a1c54716f6ab30d6c5994ba01`, the checkpoint SHA-256 is `862d16eeb4c98b58e3bea06330a8895b82a86820c91205b7ba154f707279b1f7`, and the rows SHA-256 is `d8e921d46b5067c1e12cacbd3eff930f5acb48c27192ed2527cef3159e3c9955`. The manifest status is `completed`, the checkpoint status is `completed`, and `ranked_selected_modes` contains 64 entries. The artifact records `truth_used=false`, `evaluator_used=false`, and `full_domain_complete=false`.

Across both starts, the proposal statuses were 131 `improved`, 95 `no_improvement`, and 6 `invalid_water`. Start stop reasons were 95 `no_improvement`, 25 displacement thresholds, 6 invalid-water outcomes, and 2 step limits. The final assignment set differed from its initial assignment set in 87 of the 128 starts. These counts describe the selected-mode diagnostic and do not establish a whole-domain optimum.
