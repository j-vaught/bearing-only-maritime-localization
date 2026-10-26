# Storage audit on 26 October 2026

**Scope**

The authoritative project root was `/home/jvaught2/projects/bearing-only-maritime-localization` on `comech-2080`. This audit preserved source files, raw search inputs, preparation evidence, manifests, results, branches, worktrees, and active computation. The existing `docs/accuracy-implementation-ledger.md` and the prior A41 deduplication plan at `.agent-work/worktrees/a41/.agent-work/outputs/a41/dedup-plan.json` were inspected before acting. The prior plan recorded the completed archive deduplication wave, so no duplicate deduplication job was started.

**Free-space readings**

The first reading during this audit was `45,728,198,656` available bytes. Before cleanup, a later reading was `49,553,752,064` bytes. After removing the three inactive regenerable environments, the reading was `49,603,547,136` bytes. After the verified hardlink replacements and `sync`, the reading was `50,010,288,128` bytes. A final read-only check returned `50,010,259,456` bytes. All readings remained above the required `40 GiB` reserve.

The cleanup window therefore increased measured free space by `456,536,064` bytes, approximately `456.5 MB` or `435.4 MiB`. The three environment paths had `3,976,351,744` bytes of allocated file blocks before removal, but most of their contents were already shared hardlinks, so their observed free-space increase was only `49,795,072` bytes. The four hardlink replacements had an allocated-block reclaim estimate of `410,456,064` bytes, while the observed free-space increase across that step was `406,740,992` bytes. The approximately `456.5 MB` cleanup result is based on these before and after readings. The larger increase from the first reading to the final reading was `4,282,060,800` bytes and includes unrelated background filesystem activity and computation state changes; it is not attributed to this cleanup.

**Immutable duplicate replacements**

The following four destinations were full-hash verified against the listed canonical files before replacement. Every before and after SHA-256 value was identical. Each destination retained its original path and became a hardlink to the canonical inode. The catalog hash was `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704` with size `148,891,502` bytes. The map hash was `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93` with size `56,326,616` bytes.

The destination `.agent-work/outputs/a26-wedge-complete-07d8036/static-inputs/catalog.json` was linked to `.agent-work/outputs/a26-pilot-anchor-corrected/static-inputs/2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704.json`. Its pre-replacement allocation was `148,897,792` bytes, and its before and after hash was `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`.

The destination `.agent-work/outputs/a26-wedge-geodesic-diagnostic/static-inputs/catalog.json` was linked to `.agent-work/outputs/a26-pilot-anchor-corrected/static-inputs/2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704.json`. Its pre-replacement allocation was `148,897,792` bytes, and its before and after hash was `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`.

The destination `.agent-work/outputs/a26-wedge-complete-07d8036/static-inputs/map.json` was linked to `.agent-work/outputs/a26-pilot-anchor-corrected/static-inputs/cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93.json`. Its pre-replacement allocation was `56,332,288` bytes, and its before and after hash was `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`.

The destination `.agent-work/outputs/a26-wedge-geodesic-diagnostic/static-inputs/map.json` was linked to `.agent-work/outputs/a26-pilot-anchor-corrected/static-inputs/cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93.json`. Its pre-replacement allocation was `56,328,192` bytes, and its before and after hash was `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`.

The resulting inode for both catalog destinations was `100823902` with link count `4`. The resulting inode for both map destinations was `100823901` with link count `4`. The replacement used temporary hardlinks and atomic path replacement, then verified inode identity and retained the original destination paths.

**Removed regenerable environments**

The removed paths were `.agent-work/worktrees/a27/.venv`, `.agent-work/worktrees/a01/.venv`, and `.agent-work/worktrees/a03/.venv`. Each directory contained both `pyvenv.cfg` and `CACHEDIR.TAG`, and each was confined to its worktree environment directory. Their file counts, apparent sizes, and allocated sizes were respectively `6,736`, `3,280,922,333`, and `3,296,292,864` for A27; `3,556`, `310,566,587`, and `318,951,424` for A01; and `3,863`, `352,019,965`, and `361,107,456` for A03. A process-command scan found no active process using any of these three environment paths immediately before removal. No worktree or branch was removed.

**Active-writer exclusion**

The active cache-generation output was excluded from all scans and mutations. The excluded output root was `.agent-work/outputs/a26-screened-cache-diagnostic-v3`, including its `static-inputs` and `cache-build` paths. The active processes used `.agent-work/worktrees/a26-screened-diagnostic` and `.agent-work/worktrees/a26-cachegen-818d4c7`. At the final exclusion check, the cache-generation controller and search processes were PIDs `1558230`, `1558239`, `1558384`, and `1558388`, with the output root and input files still referenced by their command lines. No active writer referenced any of the four deduplicated destinations.

**Verification**

The project integration worktree remained on commit `0f68717a0f7ceeb07b900945733ab2c943bc07ac` and was not modified. This report was created on the isolated branch `storage/audit-20261026`. The report records the exact cleanup actions and does not claim that the background free-space increase is cleanup reclaim.

Authored by J.C. Vaught.
