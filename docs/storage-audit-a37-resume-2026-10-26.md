# A37 resume smoke storage audit on 26 October 2026

**Scope**

The authoritative project root was `/home/jvaught2/projects/bearing-only-maritime-localization` on `comech-2080`. This pass inspected only `.agent-work/outputs/a37-resume-smoke-1002d733/attempt-1` and `attempt-2`. No evidence file, manifest, run result, source file, environment, or active full-run path was deleted or modified. The immutable canonical files were under `.agent-work/outputs/a37-prefix-smoke-1ef881b`.

Both resume attempts were complete bounded smoke runs. Their manifests reported `external_supervisor.status` as `completed`, `request_complete` as true, `selected_request_complete` as true, zero unresolved candidates, zero failed candidates, and one completed selected candidate. The manifests remain separate and were not changed. No process command line referenced either resume attempt before the hardlink operation.

**Full-run exclusion**

The active exact full run was excluded from all mutations. Its output root was `.agent-work/outputs/a26-cached-exact-full-1002d733`, with generation worktree `a26-cachegen-33fded7`, scoring worktree `a26-scoring-0d34a17`, and resumable worktree `a26-resumable`. The active process group had PIDs `2087843`, `2087851`, `2087854`, and `2087862`. It read the canonical prefix-smoke plan path, which was left intact. Adding hardlinks to that canonical file did not change its contents or path.

**Full-hash identity**

The plan file was `410,044,982` bytes with SHA-256 `3746ed932e21c445ebae26756cdf057413303074e2345f61daefdbcf99900349`. The inventory file was `58,795,361` bytes with SHA-256 `1c5ef3829bf4f3269523f1e1668b8aa0c3225a27591860b7f7305f7ba6478432`. The disposition file was `194,693,606` bytes with SHA-256 `1db3776bdb4ecefe0067745b6b7b09fb69dd48e7b5c2a05d7ef9ab6c77b72578`. Each hash matched across the canonical path and both resume attempts before replacement.

The canonical plan `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-plan.jsonl` now has hardlinks at both `.agent-work/outputs/a37-resume-smoke-1002d733/attempt-1/candidate-plan.jsonl` and `.agent-work/outputs/a37-resume-smoke-1002d733/attempt-2/candidate-plan.jsonl`. The canonical inventory `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-inventory.jsonl` now has hardlinks at both corresponding `attempt-1/candidate-inventory.jsonl` and `attempt-2/candidate-inventory.jsonl` paths. The canonical disposition `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-dispositions.jsonl` now has hardlinks at both corresponding `attempt-1/candidate-dispositions.jsonl` and `attempt-2/candidate-dispositions.jsonl` paths.

Post-operation inode checks gave link count `4` for the canonical and two resume plan paths, link count `4` for the canonical and two resume inventory paths, and link count `3` for the canonical and two resume disposition paths. Every original path remains present, and the six resume paths retain their manifest-referenced names.

**Free-space accounting**

The first reading was `44,949,225,472` available bytes. Immediately before replacement, the reading was `44,949,200,896` bytes. After hardlink replacement and `sync`, it was `45,807,448,064` bytes. The measured cleanup increase was `858,247,168` bytes. The destination path allocation sum was `1,327,095,808` bytes, but the plan and inventory destinations already shared one inode between attempts before this pass. The physical reclaim therefore consisted of one old plan allocation, one old inventory allocation, and two old disposition allocations, totaling the measured `858,247,168` bytes.

The `24,576` byte decrease between the first and pre-operation readings is background movement. A later reading during the still-active full run was `45,418,934,272` bytes, a decrease of `388,513,792` bytes from the post-cleanup reading. That later decrease is attributed to the active full-run computation and is not subtracted from the measured cleanup reclaim. The reserve floor of `40 GiB` remained satisfied at every reading.

**Verification**

The operation used temporary hardlinks, atomic path replacement, full-hash checks, inode checks, and `sync`. No deletion was performed. The integration worktree was not modified. This report was authored on the isolated branch `storage/audit-a37-resume-20261026`.

Authored by J.C. Vaught.
