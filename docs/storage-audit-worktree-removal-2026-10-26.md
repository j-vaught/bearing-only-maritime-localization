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



## Bounded expansion-runner triage

A new active assignment expansion process was present at 2026-10-26T12:53:10Z. Process 2613750 ran from .agent-work/worktrees/assignment-expansion-runner with the root .venv Python executable. Its command consumed assigned-bearing refinement and A20 outputs, wrote assignment-expansion-diagnostic-v1, and held the project benchmark lock. The runner, its shell parent, its working directory, its output paths, its lock, the root environment, and all named protected worktrees were excluded from cleanup. No active process map, current directory, executable, or file descriptor was used as a removal target.

The available-space reading was 44,853,968,896 bytes at 12:53:10Z and 44,852,613,120 bytes at 12:54:13Z. The observed decline of 1,355,776 bytes is not attributed to the active computation.

The remaining nonprotected worktree inventory was screened for exact remote tip equality, clean tracked and untracked state, and top-level tracked content. Forty-five clean, fully pushed worktrees remained in the candidate inventory. They were a01, a02, a03, a04, a04-solver-correction, a04-wedge-projection, a05, a07, a08-coast-base, a09, a10, a11, a11-coverage-certificate, a12, a13, a13-geodesic-reuse, a15, a16, a16-geodesic, a17, a17-legacy-regression, a18, a19, a23, a24, a25, a28, a29, a31, a32, a33, a33-acceptance, a33-calibration-binding, a33-geodesic-seeded, a33-proposal-cache, a33-static-session, a33-support-compat, a33-wedge-complete, a33-wedge-geodesic, a38, storage-audit, storage-audit-a37, storage-audit-a37-resume, and storage-audit-envs.

Every one of those 45 worktrees contains tracked data/ and results/ paths. Several also contain tracked benchmarks/, reports/, or .agent-work/ experiment records. These paths include preserved inputs, experiment outputs, manifests, source pins, and configuration material. Removing any of these worktrees would delete a data, evidence, result, or source-pinned checkout path, so none met the current cleanup rule. The protected A21, A27, A35, A37, A39, assignment expansion, assigned bearing refinement, A20, A26, root environment, and shared cache paths were also left untouched.

No worktree was removed in this triage. No branch, source, dataset, evidence, cache, manifest, configuration, result, or process was modified. No global free-space change was attributed to background activity. Further cleanup requires a changed scope that permits removal of these tracked data and result checkout copies.


## Below-reserve read-only triage and proposed duplicate checkouts

At 2026-10-26T13:31:08Z, the project filesystem reported 41,636,909,056 available bytes. At 2026-10-26T13:32:39Z it reported 41,636,192,256 bytes, and at 2026-10-26T13:33:25Z it reported 41,636,147,200 bytes. The filesystem type is ext4. The latest reading is 1,313,525,760 bytes below the 42,949,672,960-byte 40 GiB floor. The observed 761,856-byte decline between the first and latest readings is concurrent filesystem activity of unknown origin.

The current A35 assignment-expansion evaluator remains active. PIDs 2735510 and 2735517 use the root Python environment and write the assignment expansion diagnostic and full audit outputs. Their current directories, executables, descriptors, and memory maps contain zero pathnames under each proposed checkout below. Those active paths and all current output directories remain excluded.

A bounded allocation read found 79,490,920,448 bytes under .agent-work. The largest top-level allocations were outputs at 54,612,570,112 bytes, worktrees at 19,732,525,056 bytes, cache at 4,389,777,408 bytes, archive at 473,063,424 bytes, and env at 122,372,096 bytes in the depth-one summary. The detailed environment allocation is 3,234,676,736 bytes for env/a36 and 286,236,672 bytes for env/a41. The apparent sizes of those environments are mostly hardlinked package files. The verified unique nlink-one allocations are only 17,145,856 bytes for env/a36 and 3,006,464 bytes for env/a41. Their opened-library reports were aliases to the root environment, not actual process path dependencies. Neither environment was removed. The shared UV cache is 3,491,037,184 bytes and the raw map and manifest cache is 898,736,128 bytes. Both were preserved. The archive and map directories contain frozen evidence, manifests, or source material and were preserved.

The following clean, fully pushed checkouts are concrete proposed reclaim candidates for parent confirmation. No checkout was removed in this pass. Each candidate retains its branch and commit in the common Git repository. The unique allocation estimate counts only files with link count one, so it does not count shared package files.

| Checkout path | Branch and commit | Remote tip match | Allocated bytes | Unique nlink-one bytes | Runtime pathname check |
| --- | --- | --- | ---: | ---: | --- |
| .agent-work/worktrees/a04-solver-correction | accuracy/a04-solver-correction at 781caf8f993f4b2863e34ad1fee8700ee8062854 | yes | 474,288,128 | 190,607,360 | lsof zero; A35 maps, cwd, executable, and descriptors zero |
| .agent-work/worktrees/a13-geodesic-reuse | accuracy/a13-geodesic-reuse at a9fe3c659a49592e2657884eff6e2bd73b731311 | yes | 492,961,792 | 209,125,376 | lsof zero; A35 maps, cwd, executable, and descriptors zero |
| .agent-work/worktrees/a19-witness-seeds | accuracy/a19-witness-seeds at 4db70a67209604e8f0507051180e8ab9922143a2 | yes | 478,617,600 | 194,834,432 | 91 lsof paths, all device/inode/link aliases to root .venv; zero mismatches; root link counts 41 to 42; A35 maps, cwd, executable, and descriptors zero |

The three proposed checkouts have a combined unique reclaim estimate of 594,567,168 bytes, approximately 567 MiB. Including the two inactive environment candidates would raise the estimate to 614,719,488 bytes, approximately 586 MiB, while leaving the filesystem below the 40 GiB floor by approximately 699 MiB. The environments therefore do not solve the reserve deficit on their own. All three checkouts contain tracked data/ and results/ paths. They are presented as reproducible duplicate source checkouts only for explicit parent review; their source, data, and result paths were not deleted.

The earlier candidate paths a04-wedge-projection, a08-coast-base, and a11-coverage-certificate were excluded because reports or map manifests reference them. The shared UV cache, raw map cache, archived evidence, current outputs, active worktrees, root environment, and all frozen inputs remain untouched. No process was stopped. No source, dataset, result, evidence, manifest, branch, or remote ref was modified. No deletion occurred because this turn required concrete candidates to be presented before any removal.

## Approved three-checkout removal and next candidate audit

The parent approved removal of exactly three previously audited clean and fully pushed duplicate checkouts. A final pre-removal check at 2026-10-26T13:35:55Z reported 41,635,295,232 free bytes. Each branch tip matched its remote tip, each checkout had zero tracked or untracked status lines, and the active A35 evaluator had zero map, current-directory, executable, or descriptor pathnames under the three targets. The three normal non-force commands were:

```
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree remove /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/a04-solver-correction
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree remove /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/a13-geodesic-reuse
git -C /home/jvaught2/projects/bearing-only-maritime-localization worktree remove /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/a19-witness-seeds
```

The measured physical free-space changes were as follows.

| Removed checkout | Branch and commit retained | Free bytes before | Free bytes after | Measured physical reclaim |
| --- | --- | ---: | ---: | ---: |
| a04-solver-correction | accuracy/a04-solver-correction at 781caf8f993f4b2863e34ad1fee8700ee8062854 | 41,635,237,888 | 41,827,536,896 | 192,299,008 |
| a13-geodesic-reuse | accuracy/a13-geodesic-reuse at a9fe3c659a49592e2657884eff6e2bd73b731311 | 41,827,536,896 | 42,038,689,792 | 211,152,896 |
| a19-witness-seeds | accuracy/a19-witness-seeds at 4db70a67209604e8f0507051180e8ab9922143a2 | 42,038,689,792 | 42,235,486,208 | 196,796,416 |

The measured total reclaim was 600,248,320 bytes. The later reading at 2026-10-26T13:39:19Z was 42,006,716,416 bytes, a concurrent decline of 228,769,792 bytes from the post-removal reading. That background change is not attributed to the removals. All three branch refs remain present, and no source file in the common repository was modified.

The next four concrete candidates pass the same initial checks. Each is clean, its local commit matches the remote tip, its active-process pathname count is zero, its only checkout symlinks are virtual-environment interpreter links, and no report, output, archive, cache, or diagnostic path contains the checkout name. Their lsof scans returned zero paths, so no hardlink alias verification was needed.

| Candidate checkout | Branch and commit | Allocated bytes | Unique nlink-one bytes | Active process refs | External path references |
| --- | --- | ---: | ---: | ---: | ---: |
| a33-geodesic-seeded | accuracy/a33-geodesic-seeded at 1d09df551f377f39dd3a3cef82330ee8b149fd78 | 479,485,952 | 195,698,688 | 0 | 0 |
| a33-proposal-cache | accuracy/a33-proposal-cache at ab29f4a8ee0f980ad49347183cde3c1cf82733ae | 489,345,024 | 205,529,088 | 0 | 0 |
| a33-wedge-complete | accuracy/a33-wedge-complete at 639fcfdb9b25783a5c8a6ce6070d8bb056a723a8 | 465,678,336 | 181,927,936 | 0 | 0 |
| a33-wedge-geodesic | accuracy/a33-wedge-geodesic at dc6a4f491cb4d90ebbb097a19e39cf334ca5e7dd | 465,985,536 | 182,235,136 | 0 | 0 |

The four candidates have a combined unique allocation estimate of 765,390,848 bytes. Their apparent allocated total is 1,900,494,848 bytes, but shared package files were excluded from the unique estimate. Removing all four would still leave approximately 999 MiB of additional reserve shortfall at the latest reading, so further candidates would be needed to reach 44 GB. No further removal was performed pending parent review.

The following paths were not proposed. a04-wedge-projection, a11-coverage-certificate, a33-calibration-binding, a33-static-session, a33-support-compat, and a36-session-3185116 have external report or shared-cache editable-path references. a08-coast-base has immutable map-manifest source paths. The A32, A35, A37, A39, A26, assignment-expansion, parallel-expansion, root, shared-cache, root-environment, and frozen-output paths remain protected.

## Additional approved cleanup and stopping point

The parent authorized continuation under the same duplicate-checkout rule. A final pre-removal check at 2026-10-26T13:41:55Z reported 42,754,174,976 free bytes. The following three checkouts were clean, fully pushed, inactive, had no absolute path references in reports, outputs, archives, caches, or diagnostics, and had zero active process map, current-directory, executable, or descriptor references. Their only ignored content was regenerable test or bytecode cache, virtual-environment content, and one empty zero-byte .agent-work/report.tmp file in a33-static-session.

| Removed checkout | Branch and commit retained | Free bytes before | Free bytes after | Measured physical reclaim |
| --- | --- | ---: | ---: | ---: |
| a33-acceptance | accuracy/a33-acceptance at 14abb98db36847b2f99e85dce1e6e27f0811ecd8 | 42,754,166,784 | 42,891,722,752 | 137,555,968 |
| a33-static-session | accuracy/a33-static-session at 041abeb8e6426d898a7de29ac65cd1262f092d50 | 42,891,722,752 | 43,075,112,960 | 183,390,208 |
| a33-support-compat | accuracy/a33-support-compat at 6a68fda995380e632aedeaee64619c173d21c0c1 | 43,075,112,960 | 43,265,105,920 | 189,992,960 |

The measured total reclaim was 510,939,136 bytes. The net reading change across the three commands was 510,930,944 bytes; the 8,192-byte difference is concurrent filesystem activity. The later reading at 2026-10-26T13:42:08Z was 43,265,101,824 bytes. The three branch refs remain present, and no common-repository source was modified.

The cumulative measured reclaim in this cleanup sequence is 1,884,401,664 bytes. The current free reading is 1,234,898,176 bytes below the 44.5 GB target and 315,428,864 bytes above the 40 GiB floor.

No additional checkout met the same strict rule after this pass. The remaining large inactive candidates a04-wedge-projection, a11-coverage-certificate, a33-calibration-binding, and a36-session-3185116 have shared UV cache editable-finder files containing absolute source paths into those checkouts. Removing their directories would leave persistent shared-cache path mappings, so they were retained. a08-coast-base has immutable map-manifest source paths and was retained. The A37 integration and parallel-review checkouts have shared-cache path mappings and remain protected. The A32, A35, A39, A26, assignment-expansion, reviewer, parallel-expansion, root-environment, shared-cache, frozen scorer configuration, output, dataset, and evidence paths remain protected. The small older checkouts were not removed because each has shared UV cache editable-finder path mappings.

No further safe reclaimable duplicate checkout was identified. No cache, dataset, output, source, evidence, manifest, branch, or remote ref was deleted or modified. The cleanup stopped below the 44.5 GB target because the remaining candidates violate the path-dependency or protection rules.

## Cache-mapping-only cleanup and reserve checkpoint

The fresh reading at 2026-10-26T14:07:15Z was 41,182,044,160 free bytes, below the 40 GiB floor. The root virtual environment editable mapping pointed only to the root checkout. The candidate virtual environments were not active. Current process maps, current directories, executables, and descriptors were checked for every removed path. No candidate had an active process pathname reference. Absolute-path searches found either only shared UV editable-finder metadata or no external path. No output, archive, manifest, dataset, source-pin, or immutable artifact path referenced the removed worktrees.

The following four clean, fully pushed inactive checkouts had only shared UV editable-finder metadata as external references. Their branch tips matched their remote tips, tracked and untracked status was clean, and their regenerable environment files were not active.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a04-wedge-projection | accuracy/a04-wedge-projection at 5828ac254eaf7e1d3133821ef92b26f5090f414a | 179,429,376 |
| .agent-work/worktrees/a11-coverage-certificate | accuracy/a11-coverage-certificate at 54f48c4a93bc11198efcfc59304d7a43ff85bb88 | 207,630,336 |
| .agent-work/worktrees/a33-calibration-binding | accuracy/a33-calibration-binding at f2bb64f22b46d18f1ebad187c0f0dd781be74407 | 166,825,984 |
| .agent-work/worktrees/a36-session-3185116 | accuracy/a36-session-3185116 at b50a362f933d1758886f3cf96d013d8b0c9187c7 | 167,915,520 |

The measured total for these four removals was 721,801,216 bytes. The shared cache files remain untouched.

The next clean, fully pushed inactive checkout had no external absolute path reference, no symlink, and no active process pathname reference.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a17-legacy-regression | accuracy/a17-legacy-regression at 73d864737ae9c7cce05fc95da6b7828983059028 | 168,153,088 |

The next nine checkouts had only shared UV editable-finder metadata as external references. Their branch tips matched their pushed refs, their status was clean, and ignored content was limited to test caches, bytecode, package metadata, or regenerable environments.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a01 | accuracy/a01 at 503e224a68d0f6c69166fe704a0b25de35d74609 | 25,833,472 |
| .agent-work/worktrees/a02 | accuracy/a02 at 1f5fa669575310625b9ec53e43b464b68a5693b8 | 25,362,432 |
| .agent-work/worktrees/a03 | accuracy/a03 at cdf9d0e20dae414589ea206be03eddcf47f10462 | 26,292,224 |
| .agent-work/worktrees/a04 | accuracy/a04 at c5f64f36a2003da0963bdce00de64c2778a2e48a | 25,653,248 |
| .agent-work/worktrees/a05 | accuracy/a05 at 769e67c58759f9d5afe95fbb6c23b5d8328fe1e4 | 25,608,192 |
| .agent-work/worktrees/a07 | accuracy/a07 at d586462dee51b9fddee1cdfe330a664bf50ce062 | 25,759,744 |
| .agent-work/worktrees/a09 | accuracy/a09 at 424af134f0803b97ec2a1b7ed282fe0cae1fb370 | 25,653,248 |
| .agent-work/worktrees/a10 | accuracy/a10 at 59a3a810f994c8abf07338e19e9e1abe05410cae | 26,456,064 |
| .agent-work/worktrees/a11 | accuracy/a11 at 68a94ccc5913e071819908c8ec881f6fc3970df6 | 25,423,872 |

The measured total for those nine removals was 232,042,496 bytes.

Five more clean, fully pushed inactive checkouts passed the same cache-only path test.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a12 | accuracy/a12 at d2e745286c1c3b210897e7fbe9b0479e5df8b9d1 | 25,477,120 |
| .agent-work/worktrees/a13 | accuracy/a13 at e409b764aeb31ad6902e91524d2b22998de45ab3 | 26,177,536 |
| .agent-work/worktrees/a15 | accuracy/a15 at 974d9bf8bd5e4d3b8d5654a13157936f81d8b5db | 25,845,760 |
| .agent-work/worktrees/a16 | accuracy/a16 at c751c07281e94d2cd7febe2f6c3f7f2b57cb42f2 | 25,726,976 |
| .agent-work/worktrees/a16-geodesic | accuracy/a16-geodesic at 4b963e357d640b17e841fc2dd847381764b0bb3c | 135,352,320 |

The measured total for those five removals was 238,579,712 bytes.

Ten final cache-mapping-only checkouts passed the same checks.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a17 | accuracy/a17 at f6108b384577c3a30546eb4f27909dd32f6530fc | 26,324,992 |
| .agent-work/worktrees/a18 | accuracy/a18 at 19582279bf7cf618371f1b003f1b9ac58b873b2f | 26,095,616 |
| .agent-work/worktrees/a23 | accuracy/a23 at aa1eee37e33a9e6c03e1225ec3cd20d669115453 | 26,161,152 |
| .agent-work/worktrees/a24 | accuracy/a24 at 4cb7dcdc11dc02d709e8ccce6ec7f7b107be06ec | 26,423,296 |
| .agent-work/worktrees/a25 | accuracy/a25 at 4d7999f127a7cca67acfc5e0791db46342829495 | 26,210,304 |
| .agent-work/worktrees/a28 | accuracy/a28 at ca9510d5981444a2d76cf285f6d46e1299efc7cb | 26,447,872 |
| .agent-work/worktrees/a31 | accuracy/a31 at 1fe707c4cfa8ffdb870281f74d12424a7bec883a | 25,862,144 |
| .agent-work/worktrees/a33 | accuracy/a33 at be0e01adad32f67e61350b4dbdd7f9001f284c87 | 28,786,688 |
| .agent-work/worktrees/a36 | accuracy/a36 at 4189bba4ce0bce3e221370c8f50835c2f6c96ce1 | 29,720,576 |
| .agent-work/worktrees/a38 | accuracy/a38 at 44b408b8c50711160a30ef569ff3b472338748dc | 29,376,512 |

The measured total for these ten removals was 271,409,152 bytes.

The final cache-mapping-only base checkout was clean, fully pushed, inactive, and had only one shared UV editable-finder reference.

| Removed path | Pushed branch and commit | Measured physical reclaim |
| --- | --- | ---: |
| .agent-work/worktrees/a19 | accuracy/a19 at d71ea5365b450969156d55a98197bdbbda43cb9a | 25,894,912 |

The total measured reclaim in this reserve pass was 1,657,880,576 bytes. The free-space reading after the pass was 44,614,053,888 bytes at 2026-10-26T14:12:00Z. This is above the 44.5 GB working target and 1,664,380,928 bytes above the 40 GiB floor. The free-space change includes only the per-removal deltas reported above; any later filesystem movement remains unattributed.

No further nonprotected cache-mapping-only candidate was identified that would provide the remaining 483,102,720 bytes (approximately 461 MiB) needed for a full 2 GiB margin above the reserve. The concrete blockers are the A06 pre-LOS checkouts, the A14 preparation and review checkouts, and the A21 analysis and pre-LOS checkouts. They are clean and pushed in several cases but remain protected by current ownership and evaluation scope. The coast-base checkout has immutable map-manifest references. The A32, A26, A35, reviewer, A37 CPU, A39, assignment-expansion, parallel-expansion, root-environment, frozen scorer configuration, output, dataset, and evidence paths remain protected.

The largest unique-link UV cache files retained for possible later cache-only review were as follows. Link count was one for each listed file.

| Size bytes | Category | Path |
| ---: | --- | --- |
| 25,094,609 | SciPy OpenBLAS shared library | .agent-work/cache/uv/archive-v0/lfZKhQdIed525Ubi/scipy.libs/libscipy_openblas-5f890258.so |
| 24,325,657 | NumPy OpenBLAS shared library | .agent-work/cache/uv/archive-v0/p9ofA8dpPYbcTgpH/numpy.libs/libscipy_openblas64_-f48b354e.so |
| 10,633,113 | NumPy extension module | .agent-work/cache/uv/archive-v0/p9ofA8dpPYbcTgpH/numpy/_core/_multiarray_umath.cpython-313-x86_64-linux-gnu.so |
| 10,207,232 | PROJ database | .agent-work/cache/uv/archive-v0/xqUN6fOnZN91yLjm/pyproj/proj_dir/share/proj/proj.db |
| 6,756,048 | SciPy HiGHS extension | .agent-work/cache/uv/archive-v0/lfZKhQdIed525Ubi/scipy/optimize/_highspy/_core.cpython-313-x86_64-linux-gnu.so |
| 6,264,161 | PROJ curl library | .agent-work/cache/uv/archive-v0/xqUN6fOnZN91yLjm/pyproj.libs/libcurl-27c1359b.so.4.8.0 |
| 5,254,185 | Shapely GEOS library | .agent-work/cache/uv/archive-v0/KOqwW5bv37QviWSg/shapely.libs/libgeos-d1e818bb.so.3.14.1 |
| 4,828,657 | PROJ library | .agent-work/cache/uv/archive-v0/xqUN6fOnZN91yLjm/pyproj.libs/libproj-bde9a34c.so.25.9.8.1 |
| 4,107,536 | Ruff index database | .agent-work/cache/uv/simple-v22/pypi/ruff.rkyv |
| 4,098,216 | NumPy index database | .agent-work/cache/uv/simple-v22/pypi/numpy.rkyv |
| 1,335,024 | SciPy index database | .agent-work/cache/uv/simple-v22/pypi/scipy.rkyv |
| 1,232,904 | Type-checker index database | .agent-work/cache/uv/simple-v22/pypi/ty.rkyv |

No UV cache file was deleted or modified. Branch refs and all unique data and evidence remain preserved in the common repository or retained paths.

## Cross-host archive triage on 2026-10-26

The archive destination was inspected before any transfer. On comech-1176, the login account is jvaught; /home/jvaught2/projects is absent, while /home/jvaught is writable. The approved destination for a future transfer is comech-1176:/home/jvaught/bearing-only-maritime-localization-archive. The host reported 179689734144 available bytes during inspection. On comech-2422, the login account is j-vaught; /home/jvaught2/projects is absent and no equivalent project archive directory was selected. That host reported 165053140992 available bytes. No SSHFS or other network mount preserving the original project paths was present. No archive directory was created and no source was copied or removed.

The source project on comech-2080 reported 44392919040 available bytes at the corresponding audit reading. The large output trees were excluded from migration. outputs/a38-corrected-dev had files written at the audit time, outputs/a38-anchor-corrected-dev-hardlinked is referenced by A26 invocation manifests, and outputs/a38-anchor-corrected-validation is referenced by validation and A26 manifests. Their original paths must remain readable.

The following clean, pushed source checkouts had no direct process pathname reference in the bounded /proc scan. They remain triage candidates only because recent commit times and current assignment ownership prevent proving inactivity from filesystem state alone. Their exact allocated bytes are recorded for owner review.

| Checkout | Allocated bytes | Branch and HEAD | External dependency evidence |
| --- | ---: | --- | --- |
| .agent-work/worktrees/a06-prelos-caller-review | 195424256 | accuracy/a06-prelos-caller-review, ef3363df0e35bce5492827db7fb8f40228c2ac8c, pushed | no direct report/output path reference or process pathname found; recent commit at 07:55 EDT requires owner inactivity confirmation |
| .agent-work/worktrees/a06-prelos-review | 493068288 | accuracy/a06-prelos-review, 6e8c497a5d53b021872d15f163171505c5f1, pushed | no direct report/output path reference or process pathname found; recent commit at 07:26 EDT requires owner inactivity confirmation |
| .agent-work/worktrees/a08-coast-base | 444063744 | accuracy/a08-coast-base, b61e694c9d1765dbca3f20f1a028bfadd95d60d5, pushed | archive map-manifest references were found; excluded |
| .agent-work/worktrees/a14-exact-next-step | 190169088 | accuracy/a14-exact-next-step, acad75d69ae865702e45745b7c0647d5d897014a, pushed | A14 assignment scope; excluded |
| .agent-work/worktrees/a14-pre-los-preparation | 492937216 | accuracy/a14-pre-los-preparation, 80870845d337b5e26c947da0a2d85fba3925e5c8, pushed | A14 preparation scope; excluded |
| .agent-work/worktrees/a14-prepared | 433377280 | accuracy/a14-prepared, a2545cd63664d1ebc560a12bcce4cf9ab570e440, pushed | A14 prepared context; excluded |
| .agent-work/worktrees/a14-screen | 478785536 | accuracy/a14-screen, 9feab8cad1c01c3806cf85a2698a9c35363d5475, pushed | A14 screening scope; excluded |
| .agent-work/worktrees/a20 | 323960832 | accuracy/a20, 955c502445ae416d55d6e5d29fae4dc8f0b74d12, pushed | A20 scope; excluded |
| .agent-work/worktrees/a21-prelos-review | 193753088 | accuracy/a21-prelos-review, 0ace4e87c515cfcac8658066c6db701ad61cde19, pushed | A21 review scope; excluded |
| .agent-work/worktrees/a21-prelos-vector-plan | 200503296 | accuracy/a21-prelos-vector-plan, ad2d4e6f8eb11d38643f4ecdc79bb7f9da7264ae, pushed | A21 analysis scope; excluded |
| .agent-work/worktrees/a27 | 26038272 | accuracy/a27, 172e960c45b4b2eb7e0d4e691899c64d60110e2e, pushed | A27 scope; excluded |
| .agent-work/worktrees/a29 | 323952640 | accuracy/a29, 5213b4ec5fae16899ce340e11e4c563b0c7702ba, pushed | assignment profiling scope; excluded |
| .agent-work/worktrees/a34 | 137420800 | accuracy/a34, 29f71a6690b3b9fdd671feaafcbef5afe0c5dda0, pushed | A34 scope; excluded |
| .agent-work/worktrees/a37-parallel-driver-review | 191164416 | accuracy/a37-parallel-driver-review, d12fd69883a68b9d774cdac87ed77b55c5bee909, pushed | A37 scope; excluded |
| .agent-work/worktrees/a38-provenance-strict | 476635136 | accuracy/a38-protocol-row, 7b13f9cae4408e5585ad276a3b8ccb7b82ea78e8, pushed | A38 provenance scope; excluded |
| .agent-work/worktrees/a39-a45-map-diagnostic | 166031360 | accuracy/a39-a45-map-diagnostic, 28599e9bb60f6e3869841f30622f46aacd30c3b3, pushed | A39 map scope; excluded |
| .agent-work/worktrees/a39-cached-progress | 193953792 | accuracy/a39-cached-progress, dd76259969dfcf9e6800ce583d77cf8fbee9cadf, pushed | A39 cached scope; excluded |
| .agent-work/worktrees/a39-development-geometry | 166387712 | accuracy/a39-development-geometry, ea7118ba48dde48ac939ba40bbf32903f4281323, pushed | A39 geometry scope; excluded |
| .agent-work/worktrees/a39-publication | 165691392 | accuracy/a39-publication, 94a056a8cbbc0522dfebfeea7944eda18398c6c0, pushed | publication input scope; excluded |
| .agent-work/worktrees/a39-repair-checkpoint | 167104512 | accuracy/a39-repair-checkpoint, f3c2d84e5ec81a3d778e45f7e9e4bea4a92e15a7, pushed | A39 checkpoint scope; excluded |
| .agent-work/worktrees/a41 | 488939520 | accuracy/a41, c2ebc7e0402a3a45bf86241985ae006f79392401, pushed | validation/archive scope; excluded |

The only direct live worktree references in the bounded process scan were an A26 command-line reference and A37 current working directories. Those worktrees remain protected. Because no candidate has passed the inactive-owner check and no original path can be replaced by a mounted archive, no transfer was authorized by this audit. Consequently, no source or destination SHA-256 manifest exists, no bytes were reclaimed, and the source free-space reading is unchanged by this pass. A future transfer must first preserve a per-file SHA-256 manifest, copy to the approved 1176 destination, verify destination hashes and byte counts, and only then remove an explicitly approved inactive checkout while retaining its branch ref and a retrieval manifest.
