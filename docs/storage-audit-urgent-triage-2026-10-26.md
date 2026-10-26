# Urgent storage triage on 2026-10-26

## Read-only allocation findings

The remote project filesystem reported 43,208,630,272 available bytes at 2026-10-26T11:15:00Z and 43,072,241,664 bytes at 11:16:19Z. The active exact run was still present at the first reading and ended before the second cleanup pass. Its project output was 728,850,432 bytes allocated, including a 192,212,787-byte candidate-disposition file, and was preserved.

The repository work area measured 76,854,059,008 bytes allocated at 11:15:00Z. The largest repository components were the corrected development outputs at 30,244,245,504 bytes, corrected validation outputs at 17,010,176,000 bytes, corrected development outputs at 2,674,946,048 bytes, the main corrected outputs at 2,134,962,176 bytes, and the package caches at 4,389,609,472 bytes. The default package cache outside the repository measured 15,996,358,656 bytes allocated before its built-in prune.

A targeted recent-file scan found no newly written file over 100 MiB outside the known active output. The repeated map files in inactive worktrees totalled 528 files and 8,543,198,999 apparent bytes across 96 worktrees. Their SHA-256 values matched the tracked project copies, but the files have writable mode 664 and are tracked inputs. Hardlinking them would allow a later checkout or edit in one worktree to mutate another path, so they were retained. Active worktree names supplied by the owner were excluded from all candidate operations.

## Safe cleanup

After the active exact run released /home/jvaught2/.cache/uv/.lock, the built-in command /home/jvaught2/.local/bin/uv cache prune --offline --no-progress completed successfully. It removed 1,371 dangling files reported as 197.7 MiB. The measured filesystem free-space increase was 4,120,576 bytes. Allocated cache usage changed from 15,996,358,656 to 15,992,242,176 bytes.

The inactive A36 GPU session environment was then verified. The path was .agent-work/worktrees/a36-session-3185116/.venv. Its branch was clean at commit b50a362 with subject test GPU static session parity across devices. The environment markers pyvenv.cfg and the directory were present, no process command line, current working directory, executable, or open descriptor referenced the environment, and no project process remained. The environment measured 3,234,816,000 bytes before removal. It was removed with Python shutil.rmtree as an explicitly verified regenerable environment. Free space changed from 43,076,214,784 to 43,097,157,632 bytes, reclaiming 20,942,848 bytes on the filesystem.

The repository cache .agent-work/cache/uv was checked after the A37 test ended. Its lock had no holder, but the built-in prune reported no unused entries and changed neither free space nor allocated size. No active output, source, dataset, evidence, result, protected worktree, or current environment was modified.

The final reading at 2026-10-26T11:17:34Z was 43,097,124,864 available bytes. This is approximately 40.137 GiB and leaves about 140.8 MiB above the 40 GiB floor. Concurrent activity accounts for differences between the measured cleanup deltas and the net free-space change. No further safe deletion was identified in this pass.

Written authorship is J.C. Vaught.

