# Regenerable environment storage audit on 26 October 2026

**Scope**

The authoritative project root was `/home/jvaught2/projects/bearing-only-maritime-localization` on `comech-2080`. The audit inspected live processes and worktree status, then removed only explicitly selected `.venv` directories from clean, old worktrees. It did not touch the root environment, shared caches, source files, data, outputs, benchmark-lock state, or protected current worktrees. The protected names included A26 resumable work, A35 cached evaluation, A37 review work, A14 work, A21 work, and A39 work.

**Free-space accounting**

The first reading was `46,382,653,440` available bytes. Immediately before removal, the reading was `46,382,448,640` bytes, or `43.2436 GiB`. Immediately after removal and `sync`, the reading was `46,828,638,208` bytes, or `43.6590 GiB`. The required `40 GiB` reserve is `42,949,672,960` bytes, leaving `3,878,965,248` bytes above the reserve after cleanup. The `204,800` byte decrease before removal is background filesystem movement and is not attributed to cleanup.

The selected environment files had a pre-removal sum of `2,157,158,400` allocated bytes when counting files whose link count was one. That value is a file-block estimate and does not equal unique physical extents on this filesystem. The authoritative observed free-space increase from the before and after `df` readings was `446,189,568` bytes, approximately `446.2 MB` or `0.416 GiB`. The report therefore treats `446,189,568` bytes as measured reclaim and records the larger per-file estimate separately.

**Eligibility checks**

Every selected worktree was older than seven hours at inspection, had a clean `git status --porcelain`, and contained both `pyvenv.cfg` and `CACHEDIR.TAG` below the selected `.venv` path. A `/proc` scan found no process whose current working directory or executable path referenced any selected environment. The selected paths were regenerable environment directories only. No worktree directory, branch, source file, or result file was removed.

**Removed paths and pre-removal estimates**

The following table records every removed environment path. The byte column is the pre-removal allocated-block estimate for files with link count one. The post-removal state is absent for every path.

| Worktree | Removed path | Estimated allocated bytes |
| --- | --- | ---: |
| A05 | `.agent-work/worktrees/a05/.venv` | 78,245,888 |
| A04 | `.agent-work/worktrees/a04/.venv` | 78,663,680 |
| A02 | `.agent-work/worktrees/a02/.venv` | 78,245,888 |
| A31 | `.agent-work/worktrees/a31/.venv` | 80,338,944 |
| A11 | `.agent-work/worktrees/a11/.venv` | 71,516,160 |
| A13 | `.agent-work/worktrees/a13/.venv` | 78,663,680 |
| A07 | `.agent-work/worktrees/a07/.venv` | 78,663,680 |
| A12 | `.agent-work/worktrees/a12/.venv` | 71,516,160 |
| A16 | `.agent-work/worktrees/a16/.venv` | 78,290,944 |
| A09 | `.agent-work/worktrees/a09/.venv` | 78,290,944 |
| A19 | `.agent-work/worktrees/a19/.venv` | 78,663,680 |
| A10 | `.agent-work/worktrees/a10/.venv` | 78,663,680 |
| A15 | `.agent-work/worktrees/a15/.venv` | 76,414,976 |
| A17 | `.agent-work/worktrees/a17/.venv` | 85,118,976 |
| A18 | `.agent-work/worktrees/a18/.venv` | 79,245,312 |
| A25 | `.agent-work/worktrees/a25/.venv` | 85,118,976 |
| A23 | `.agent-work/worktrees/a23/.venv` | 79,245,312 |
| A24 | `.agent-work/worktrees/a24/.venv` | 79,245,312 |
| A22 | `.agent-work/worktrees/a22/.venv` | 79,245,312 |
| A28 | `.agent-work/worktrees/a28/.venv` | 79,638,528 |
| A35 | `.agent-work/worktrees/a35/.venv` | 79,245,312 |
| A32 | `.agent-work/worktrees/a32/.venv` | 85,118,976 |
| A33 | `.agent-work/worktrees/a33/.venv` | 85,118,976 |
| A38 | `.agent-work/worktrees/a38/.venv` | 85,155,840 |
| A34 | `.agent-work/worktrees/a34/.venv` | 85,118,976 |
| A16 geodesic | `.agent-work/worktrees/a16-geodesic/.venv` | 79,245,312 |
| A33 acceptance | `.agent-work/worktrees/a33-acceptance/.venv` | 85,118,976 |

The old clean worktree `.agent-work/worktrees/a34-coast-base/.venv` was retained after the target was reached. The active and protected worktrees were not candidates for this pass, even when their environments were present.

**Verification**

The integration worktree was unchanged at commit `3d030f1b32cd72b5b147d4d0e09fe8f387b4910f`. The storage operation was performed from an isolated branch named `storage/audit-envs-20261026`. A final path check confirmed that all 27 selected `.venv` directories were absent while their parent worktrees remained present. The report distinguishes the measured `df` reclaim from the per-file block estimate and from the background change before removal.

Authored by J.C. Vaught.
