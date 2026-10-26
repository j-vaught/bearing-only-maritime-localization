# A37 smoke storage audit on 26 October 2026

**Scope**

The authoritative project root was `/home/jvaught2/projects/bearing-only-maritime-localization` on `comech-2080`. This narrow pass inspected only the completed directories `.agent-work/outputs/a37-prefix-smoke-f004601` and `.agent-work/outputs/a37-prefix-smoke-1ef881b`. It did not modify source files, environments, active profiling work, unmatched outputs, or any raw search evidence. No clearly named completed A37 timeout smoke inventory was found, so no additional directory was inspected or changed.

**Free-space accounting**

The first reading for this pass was `46,892,822,528` available bytes. Immediately before the hardlink operation, the reading was `46,892,777,472` bytes, or `43.6723 GiB`. Immediately after the operation and `sync`, the reading was `47,361,630,208` bytes, or `44.1090 GiB`. The required `40 GiB` reserve remained available throughout. The small `45,056` byte decrease between the first and pre-operation readings is background filesystem movement and is not attributed to the audit.

The two duplicate destination files had an allocated-block reclaim estimate of `468,852,736` bytes. The observed free-space increase across the operation was exactly `468,852,736` bytes, approximately `468.9 MB` or `0.4367 GiB`. The allocated reclaim and the observed free-space change therefore agree for this pass.

**Verified hardlinks**

The canonical plan file was `.agent-work/outputs/a37-prefix-smoke-f004601/candidate-plan.jsonl`. Its destination was `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-plan.jsonl`. Both files were `410,044,982` bytes before replacement, had matching mode `0664`, owner `1002`, and group `1002`, and had the full SHA-256 hash `3746ed932e21c445ebae26756cdf057413303074e2345f61daefdbcf99900349`. The destination had `410,050,560` allocated bytes before replacement.

The canonical inventory file was `.agent-work/outputs/a37-prefix-smoke-f004601/candidate-inventory.jsonl`. Its destination was `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-inventory.jsonl`. Both files were `58,795,361` bytes before replacement, had matching mode `0664`, owner `1002`, and group `1002`, and had the full SHA-256 hash `1c5ef3829bf4f3269523f1e1668b8aa0c3225a27591860b7f7305f7ba6478432`. The destination had `58,802,176` allocated bytes before replacement.

The post-operation plan paths share inode `98569049` with link count `2`, and the post-operation inventory paths share inode `98569047` with link count `2`. Full hashes were recomputed before replacement and remained unchanged after replacement. Both original destination paths were retained.

**Disposition preservation**

The disposition files were deliberately excluded. `.agent-work/outputs/a37-prefix-smoke-f004601/candidate-dispositions.jsonl` is `194,693,572` bytes with SHA-256 `57d1c336b9d9ed956d5430f7cc4c195f831862f9d6325de039d757bf8ae480a1`. `.agent-work/outputs/a37-prefix-smoke-1ef881b/candidate-dispositions.jsonl` is `194,693,606` bytes with SHA-256 `1db3776bdb4ecefe0067745b6b7b09fb69dd48e7b5c2a05d7ef9ab6c77b72578`. Their differing sizes and hashes show that their dispositions remain distinct. Neither file was altered.

**Writer exclusion and verification**

No process command line referenced either smoke directory, either candidate file, or the smoke directory name before mutation. Both directories were complete, with no recent writer activity during the final check. The operation used temporary hardlinks, atomic path replacement, inode verification, and `sync`. The integration worktree remained unchanged at commit `f9f40678a2a2246f1dd0953e0cb38ff36d926168`. This report was authored on the isolated branch `storage/audit-a37-20261026`.

Authored by J.C. Vaught.
