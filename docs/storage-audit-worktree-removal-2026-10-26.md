# Storage worktree removal audit on 2026-10-26

## Scope and verification

This pass examined only the remote project at /home/jvaught2/projects/bearing-only-maritime-localization. The active refinement process was preserved. Its current working directory is .agent-work/worktrees/a20-cached-refinement and its command uses the protected root environment, the A26 scoring worktree, and the A26 cached output. No production source file was modified. The removed paths were duplicate source checkouts whose source remains in the recorded Git commits and branch refs. No active process, lock, dataset, result directory, branch ref, or common Git object was modified.

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

The sum of individual measured reclaims is 934,232,064 bytes. The net free-space increase from the first to the last reading is 934,223,872 bytes. The 8,192 byte difference is concurrent filesystem activity. The latest reading after the pass was 45,321,539,584 bytes at 2026-10-26T11:52:50Z. Available free space declined by 194,572,288 bytes between the last removal reading and this latest reading. The decline is an observed shared-filesystem change of unknown origin. The final df -T reading reported an ext4 filesystem. The active output grew by only approximately 200 KiB in the same interval, so the decline is not attributed to that output.

The exact removal command for each row was the following command with the recorded checkout path substituted.

```
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree remove /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/<checkout>
```

The exact recreation form for each preserved branch is the following command with the recorded branch substituted.

```
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree add /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/<checkout> <branch>
```

## Exact ignored inventories

The removed a30-parallel-candidates checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, and tests/accuracy/__pycache__/.

The removed a34-coast-base checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/evaluation/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_localization/simulator/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_ingest/__pycache__/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/__pycache__/, tests/accuracy/__pycache__/, and tools/__pycache__/.

The removed a43-geodesic-wedge checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, and tests/accuracy/__pycache__/.

The removed a46 checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/__pycache__/, and tests/accuracy/__pycache__/.

The removed cached-profile checkout contained .pytest_cache/, .ruff_cache/, .venv/, bearing_localization/__pycache__/, bearing_localization/search/__pycache__/, bearing_localization/search/proposals/__pycache__/, bearing_only_maritime_localization.egg-info/, maritime_localization_shared/__pycache__/, maritime_localization_shared/contracts/__pycache__/, maritime_localization_shared/geometry/__pycache__/, maritime_localization_shared/geospatial/__pycache__/, tests/accuracy/__pycache__/, and tools/__pycache__/.

## Earlier verified removals

The same cleanup sequence previously removed .agent-work/worktrees/a28-geodesic. Its branch was accuracy/a28-geodesic at 3985c6fa0d245c7b8d94c140cbee2634e6d623e0, with the same remote tip. Its apparent size was 3,415,232,512 bytes. Free space changed from 44,210,950,144 to 44,411,527,168 bytes, a measured reclaim of 200,577,024 bytes.

It also removed .agent-work/worktrees/a36-rerun. Its branch was accuracy/a36-rerun-2792 at 0fb2d4a43f63feb254027f27a2acd8e38c94a2a9, with the same remote tip. Its apparent size was 3,385,421,824 bytes. Free space changed from 44,411,518,976 to 44,582,277,120 bytes, a measured reclaim of 170,758,144 bytes.

The combined measured reclaim from these two earlier removals was 371,335,168 bytes. Across all seven removed worktrees, the measured reclaim was 1,305,567,232 bytes. The apparent environment sizes were larger than the filesystem deltas; this report does not attribute that difference to a specific filesystem mechanism.

## Candidates retained before alias verification

During the initial pass, lsof reported mapped-library pathnames under the following clean, pushed worktrees. They were retained until the later pathname and inode audit. The paths were a25-summary, a30, a30-screened, a38-prep, a38-v2-builder, a42-eligibility, a43-los-opt, a43-pairwise, a44, a45, a47, and a13-proposal-cache.

The paths a29 and a41 contained ignored output evidence under .agent-work, including a29-assignment-refresh files and a41/dedup-plan.json, so they remain retained. The path a38-provenance-strict contains an ignored .agent-work/benchmark.lock and remains retained. The path a34 has a local tip different from its remote tip and remains retained. The detached a43-los-baseline path has no branch tip to preserve and remains retained. The unclean a08 path and all protected active or new worktrees remain retained.

The initial lsof result alone was insufficient because it could report hardlink aliases. The subsequent /proc pathname audit and device:inode:link-count comparison are recorded in the next section. No process was stopped. No branch or remote ref was deleted. Git automatic garbage collection was not run.

Written authorship is J.C. Vaught.



## Hardlink alias verification pass

A second read-only process audit examined the live refinement before removing the next set of worktrees. Process 2278118 had working directory .agent-work/worktrees/a20-cached-refinement, executable /home/jvaught2/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/bin/python3.11, and command path /home/jvaught2/projects/bearing-only-maritime-localization/.venv/bin/python. Its Python path was the A26 scoring worktree. The process maps contained 90 unique pathnames under the root .venv and zero pathnames under any candidate checkout. Its file descriptors pointed to the active refinement log, the root environment proj.db, and the project benchmark lock. The shell parent and SSH wrapper were also checked. No candidate checkout appeared in a process current directory, executable, map, or descriptor. The final df -T reading reported ext4.

The earlier lsof results for these environments were inode aliases. For each listed alias, the candidate pathname and the corresponding root .venv pathname had equal device, inode, and link-count values. The root link count was at least 2, with observed values of 52 or 53. The candidate alias was therefore not an opened or mapped pathname dependency. Every candidate was clean, its local branch tip matched its remote tip, and its ignored inventory contained only .venv, Python bytecode, test caches, and package metadata. The removal command was normal git worktree remove without force. The source remains in the recorded commit and branch ref.

| Checkout path | Branch and commit | Apparent bytes before | lsof aliases | Device:inode mismatches or root links below 2 | Actual process pathname references | Free bytes before | Free bytes after | Measured reclaim |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| .agent-work/worktrees/a25-summary | accuracy/a25-summary at ad754745d5b524afa6bbf4402e46a1bb779c7ebe | 457,497,173 | 122 | 0 | 0 | 44,426,694,656 | 44,614,492,160 | 187,797,504 |
| .agent-work/worktrees/a30 | accuracy/a30 at 90284715b24d21296a0f57c863fa304f88be4823 | 3,242,019,544 | 91 | 0 | 0 | 44,614,492,160 | 44,661,166,080 | 46,673,920 |
| .agent-work/worktrees/a30-screened | accuracy/a30-screened at b149b7afb3b114327f9a6d8c80800ef6824fc226 | 476,492,525 | 91 | 0 | 0 | 44,661,166,080 | 44,868,124,672 | 206,958,592 |
| .agent-work/worktrees/a38-prep | accuracy/a38-prep at 8996da8bf54e8835080191364d88cacc52dbd7df | 427,069,380 | 91 | 0 | 0 | 44,868,124,672 | 45,025,529,856 | 157,405,184 |
| .agent-work/worktrees/a38-v2-builder | accuracy/a38-v2-builder at 525c5e2b3457b66de6307663d5fe05e210725758 | 458,612,482 | 91 | 0 | 0 | 45,025,529,856 | 45,214,609,408 | 189,079,552 |
| .agent-work/worktrees/a42-eligibility | accuracy/a42-eligibility at 2c5b7cc9a0a4e0ae71eba420a5e336834154c921 | 443,434,456 | 91 | 0 | 0 | 45,214,605,312 | 45,388,386,304 | 173,780,992 |
| .agent-work/worktrees/a43-los-opt | accuracy/a43-los-opt at c6cbd2350359dcbf2bcbed34570902ec1c0e96c8 | 459,523,765 | 91 | 0 | 0 | 45,388,386,304 | 45,578,465,280 | 190,078,976 |
| .agent-work/worktrees/a43-pairwise | accuracy/a43-pairwise at 862a6b40b80d76bc74ac0c9e24462cb108131fb7 | 438,760,840 | 91 | 0 | 0 | 45,578,465,280 | 45,747,376,128 | 168,910,848 |
| .agent-work/worktrees/a44 | accuracy/a44-catalog-load at e0a04cb2d8b951a55234ea225241f9aca2a608e9 | 452,801,017 | 91 | 0 | 0 | 45,747,376,128 | 45,930,291,200 | 182,915,072 |
| .agent-work/worktrees/a45 | accuracy/a45-map-startup at 383c2dc8c6c3ea678af7633fe9a6806dd7154620 | 458,509,684 | 91 | 0 | 0 | 45,930,291,200 | 46,119,260,160 | 188,968,960 |
| .agent-work/worktrees/a47 | accuracy/a47-wedge-speed at 443e00362a51c61c5d24c978662e3cf9b4ca5ecc | 453,380,587 | 91 | 0 | 0 | 46,119,260,160 | 46,302,818,304 | 183,558,144 |
| .agent-work/worktrees/a13-proposal-cache | accuracy/a13-proposal-cache at 9815ca91fb0d4c0132a32d4b0d5ac2201524f5e5 | 466,472,243 | 91 | 0 | 0 | 46,302,818,304 | 46,499,614,720 | 196,796,416 |

The sum of individual measured reclaims in this alias-verification pass was 2,072,924,160 bytes. The net free-space increase from the first to the last removal reading was 2,072,920,064 bytes. The 4,096 byte difference is concurrent filesystem activity of unknown origin. The latest free-space reading was 46,499,586,048 bytes at 2026-10-26T12:02:03Z. No global free-space change was attributed to the active computation.

Across the original seven removals and these twelve alias-verified removals, the measured reclaim is 3,378,491,392 bytes. No further removal was performed after this verification pass while the active computation remained running.

