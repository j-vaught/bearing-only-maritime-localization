# Full coast search feasibility checkpoint

## Purpose and source identity

This development-only checkpoint tested startup and bounded proposal feasibility across five search methods. It did not invoke the evaluator, compute localization error, or produce an accuracy comparison. The planned pilot remains 80 searches, formed by eight preselected development cases, five methods, and candidate budgets of 64 and 256. The controller stopped after the first five searches as requested, so 75 planned calls remain unrun.

The source worktree was fixed at commit `c76be7c0f728f302f51a5acf433288512e52c684` on branch `accuracy/a26-pilot-exec-c76`. The pilot configuration SHA-256 is `382c476297046a422d9295060127dc545ee4b850fadf0830889398513b1e6adf`. The selected trial manifest SHA-256 is `8c24a1cc1783daa847bc92b637f1a584f01391063eaf4c5c1e7043632b7012be`. The authoritative eligibility manifest SHA-256 is `2330090802952093bab1d5ec6cb97c6298cce27ee2050804f6eab91d66e8d050`. The full coast catalog SHA-256 is `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`, and the experimental full coast map SHA-256 is `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`. Every method used the same declared full coast domain and scope hash `d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1`.

The selected cases contained six supported nominal rows, one unsupported nominal row, and one retained preparation failure. The first round-robin block selected the same supported nominal case at port 001, placement 001, realization zero, 10 km range, and types disabled. The per-method run manifests are under `.agent-work/outputs/a26-pilot-c76be7c/runs/<method>/budget-64/<run-id>/run-manifest.json`; raw stdout, stderr, estimates, telemetry, and preflight records remain in the server output directory `.agent-work/outputs/a26-pilot-c76be7c`.

## Checkpoint observations

Pairwise search at budget 64 reached the 300-second watchdog and was terminated as a process group. Its recorded wall time was 300.176 seconds. Wedge search at budget 64 also reached the watchdog and was terminated as a process group after 300.185 seconds. Neither timeout produced an estimate, telemetry file, or candidate counters, so these attempts do not identify which internal stage dominated.

Hierarchical search completed in 15.272 seconds. It evaluated 64 candidates, all classified as unresolved coverage, with zero scored and zero retained. Its telemetry attributed 0.382 seconds to input loading and 0.000762 seconds to candidate search. Grid search completed in 15.284 seconds and had the same 64 unresolved-coverage outcomes, with zero scored or retained. Its declared grid contained 39,840 points; input loading took 0.378 seconds and candidate search took 0.000667 seconds. Reference grid completed in 15.949 seconds. It evaluated 64 of 39,840 grid points and left 39,776 unevaluated; the evaluated points were all unresolved coverage. Its input loading took 0.384 seconds and candidate search took 0.822 seconds.

All three completed methods reported `budget_exhausted`. The first five attempts therefore establish only that the three grid-family implementations can load the full coast inputs and return bounded proposals under this configuration. They do not establish useful localization performance or completed search streams. Pairwise and wedge startup-to-result work exceeded the 300-second diagnostic limit in these attempts.

## Timing conditions and reproducibility

The shared compute node was already under substantial external CPU and GPU activity before launch. The recorded pre-run snapshot is `system-snapshot-before.txt`, and the post-run snapshot is `system-snapshot-after.txt` within the output directory. These conditions make the measured wall times diagnostic rather than clean comparative benchmarks. The first failed launch, which used different static-input paths and stopped before any search, remains in `controller.stderr.log`; the successful resumed invocation used the original pinned input paths and has its command in `invocation.txt`. The execution progress record confirms that five searches started and the controller paused with reason `stop_after_searches`.

The complete planned denominator remains 80. This checkpoint does not remove or relabel any selected cases. Search outputs and logs are preserved at the paths above, and the pilot can resume from its saved run state after review. No held-out inputs were used.
