# Storage audit cache pass on 2026-10-26

## Scope and live safety checks

This pass covered only the remote repository at `/home/jvaught2/projects/bearing-only-maritime-localization`. The active exact cache generation was left running. At 2026-10-26T11:07:21Z the filesystem reported 45,382,709,248 available bytes. The active writer remained present through the final check with controller PID 2087843, uv PID 2087851, Python controller PID 2087862, and worker descendants. The active command writes under `.agent-work/outputs/a26-cached-exact-full-1002d73` and uses worktrees `a26-resumable`, `a26-cachegen-33fded7`, and `a26-scoring-0d34a17`. None of those paths was modified.

The default uv cache lock `/home/jvaught2/.cache/uv/.lock` was held by PID 2087851. The default cache was therefore not pruned. The separate repository cache `/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/cache/uv` had no active process references in the checked active PID set and no fuser holder on its lock. The built-in command `/home/jvaught2/.local/bin/uv cache prune --cache-dir /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/cache/uv --offline --no-progress` completed with return code zero and removed 404 dangling files. Its immediate free-space increase was 3,809,280 bytes. The cache's apparent size changed from 3,456,663,090 to 3,453,932,667 bytes, while allocated usage changed from 3,494,739,968 to 3,490,811,904 bytes.

## Verified immutable hardlinks

Three completed-output duplicate pairs were fully hashed before replacement. The destination path was atomically replaced with a hardlink to the canonical path. Every path remains present and readable. No source, input, evidence, result, or active output path was removed.

| Canonical path | Preserved destination path | Size in bytes | SHA-256 |
| --- | --- | ---: | --- |
| `.agent-work/outputs/a38-anchor-corrected-validation/plan-initial25/trials.csv` | `.agent-work/outputs/a38-anchor-corrected-validation/plan/trials.csv` | 1,961,668 | `535b5d2e6b1d93e94e66584a936f932d6dd16c2d102ab3621b0e9122b46bb92f` |
| `.agent-work/outputs/a38-anchor-corrected-dev-hardlinked/eligibility/development.json` | `.agent-work/outputs/a38-anchor-corrected-dev/eligibility/development.json` | 3,824,894 | `d5cbf6d6d45fac0054fcda1e6431f87cba29f9b2daad4e1260ef6397bd43cb` |
| `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-inventory.jsonl` | `.agent-work/outputs/a37-driver-smoke-c159704/candidate-inventory.jsonl` | 58,795,361 | `1c5ef3829bf4f3269523f1e1668b8aa0c3225a27591860b7f7305f7ba6478432` |

The three pre-operation hash pairs matched exactly. The post-operation hashes matched the same values. Post-operation inode and link counts were `113811147 2` for both trials paths, `100167441 2` for both eligibility paths, and `98569047 6` for both candidate inventory paths. The measured immediate free-space increase across the hardlink operation was 64,548,864 bytes.

A targeted metadata scan of completed non-input output files larger than 64 KiB found no other same-size duplicate group besides three distinct validation wave plans. Their full hashes differed, so they were retained. Raw input and evidence trees were excluded from mutation.

## Accounting and retained candidates

The directly measured increase from the custom-cache prune and hardlink operation was 68,358,144 bytes. The immediate hardlink reading was 45,378,035,712 bytes before and 45,442,584,576 bytes after. The final reading at 2026-10-26T11:08:58Z was 45,441,740,800 bytes, or 42.3209 GiB. The 843,776-byte decrease after the hardlink reading occurred while the active computation remained running and is treated as concurrent activity. Relative to the first reading, the net observed increase was 59,031,552 bytes, so it is not attributed entirely to cleanup.

No deletion of data, source, evidence, result, environment, worktree, or active cache-generation content was performed in this pass. The default shared cache remains a candidate for a later built-in prune only after PID 2087851 releases its lock and active environment use is rechecked. The remaining large build directory and raw output trees were not removed because their ownership and regeneration safety were not established during the active computation.

Written authorship is J.C. Vaught.

