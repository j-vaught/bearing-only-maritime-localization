# Storage worktree removal audit on 2026-10-26

## Scope and verification

This pass examined only the remote project at /home/jvaught2/projects/bearing-only-maritime-localization. The active refinement process was preserved. Its current working directory is .agent-work/worktrees/a20-cached-refinement and its command uses the protected root environment, the A26 scoring worktree, and the A26 cached output. No active process, lock, source file, dataset, result directory, branch ref, or common Git object was modified.

Each removed checkout was verified immediately before removal. The tracked and untracked status was empty. The local branch tip matched the corresponding remote branch tip. The ignored inventory contained only the environment directory, Python bytecode directories, test caches, and package metadata. A recursive open-file check with lsof returned no reference to the checkout. Each removal used normal git worktree remove without force. The branch ref still resolves to the recorded commit after removal.

## Measured removals

The starting free-space reading for this operation was 44,581,888,000 bytes at 2026-10-26T11:52:30Z. The measured values below are individual available-space deltas. The active computation continued during the pass.

| Checkout path | Branch and commit | Remote tip | Apparent bytes before | Free bytes before | Free bytes after | Measured reclaim |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| .agent-work/worktrees/a30-parallel-candidates | accuracy/a30-parallel-candidates at 44da4458e27868dc9e7e5a28c8ee1f40a2ee38af | 44da4458e27868dc9e7e5a28c8ee1f40a2ee38af | 3,393,656,982 | 44,581,888,000 | 44,779,941,888 | 198,053,888 |
| .agent-work/worktrees/a34-coast-base | accuracy/a34-coast-base at 7bc886e52c71b520fb70f2a31ea660ff4849e7e8 | 7bc886e52c71b520fb70f2a31ea660ff4849e7e8 | 427,794,876 | 44,779,937,792 | 44,938,108,928 | 158,171,136 |
| .agent-work/worktrees/a43-geodesic-wedge | accuracy/a43-geodesic-wedge at 03ad4191a35b267047fccc24fc3c909ec9b7d065 | 03ad4191a35b267047fccc24fc3c909ec9b7d065 | 453,589,458 | 44,938,104,832 | 45,121,925,120 | 183,820,288 |
| .agent-work/worktrees/a46 | accuracy/a46 at db760b6a767ee79ab17a0ffd298bad3866740cfe | db760b6a767ee79ab17a0ffd298bad3866740cfe | 453,209,738 | 45,121,925,120 | 45,305,282,560 | 183,357,440 |
| .agent-work/worktrees/cached-profile | accuracy/cached-profile at 03ee598eb8cdb90a49b1e90177e90f4a4f77cdc8 | 03ee598eb8cdb90a49b1e90177e90f4a4f77cdc8 | 480,294,978 | 45,305,282,560 | 45,516,111,872 | 210,829,312 |

The sum of individual measured reclaims is 934,232,064 bytes. The net free-space increase from the first to the last reading is 934,223,872 bytes. The 8,192 byte difference is concurrent filesystem activity. The latest reading after the pass was 45,321,539,584 bytes at 2026-10-26T11:52:50Z. The active refinement consumed 194,572,288 bytes after the last removal reading.

The exact removal command for each row was the following command with the recorded checkout path substituted.

\`\`\`
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree remove /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/<checkout>
\`\`\`

The exact recreation form for each preserved branch is the following command with the recorded branch substituted.

\`\`\`
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree add /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/<checkout> <branch>
\`\`\`

## Exact ignored inventories

The removed a30-parallel-candidates checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, and tests/accuracy/__pycache__/.

The removed a34-coast-base checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/evaluation/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_localization/simulator/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_ingest/__pycache__/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/__pycache__/, tests/accuracy/__pycache__/, and tools/__pycache__/.

The removed a43-geodesic-wedge checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, and tests/accuracy/__pycache__/.

The removed a46 checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/__pycache__/, and tests/accuracy/__pycache__/.

The removed cached-profile checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/accuracy/__pycache__/, and tools/__pycache__/.

## Earlier verified removals

The same cleanup sequence previously removed .agent-work/worktrees/a28-geodesic. Its branch was accuracy/a28-geodesic at 3985c6fa0d245c7b8d94c140cbee2634e6d623e0, with the same remote tip. Its apparent size was 3,415,232,512 bytes. Free space changed from 44,210,950,144 to 44,411,527,168 bytes, a measured reclaim of 200,577,024 bytes.

It also removed .agent-work/worktrees/a36-rerun. Its branch was accuracy/a36-rerun-2792 at 0fb2d4a43f63feb254027f27a2acd8e38c94a2a9, with the same remote tip. Its apparent size was 3,385,421,824 bytes. Free space changed from 44,411,518,976 to 44,582,277,120 bytes, a measured reclaim of 170,758,144 bytes.

The combined measured reclaim from these two earlier removals was 371,335,168 bytes. Their apparent environment sizes were much larger than the filesystem deltas because the package files use shared extents.

## Candidates retained

The active process has open mapped-library references into the following clean, pushed worktrees. They were retained because the no-open-reference condition was not satisfied. The paths are a25-summary, a30, a30-screened, a38-prep, a38-v2-builder, a42-eligibility, a43-los-opt, a43-pairwise, a44, a45, and a47. The mapped files are under each checkout's .venv and include the loaded geospatial shared library.

The path a13-proposal-cache was also retained for the same mapped-library reason. The paths a29 and a41 contain ignored output evidence under .agent-work, including a29-assignment-refresh files and a41/dedup-plan.json, so they were not eligible. The path a38-provenance-strict contains an ignored .agent-work/benchmark.lock and was retained. The path a34 has a local tip different from its remote tip and was retained. The detached a43-los-baseline path has no branch tip to preserve and was retained. The unclean a08 path and all protected active or new worktrees were retained.

These blockers leave no additional candidate with both an allowed ignored inventory and a clean no-reference state that can be safely removed in this pass. No process was stopped. No branch or remote ref was deleted. Git automatic garbage collection was not run.

Written authorship is J.C. Vaught.

