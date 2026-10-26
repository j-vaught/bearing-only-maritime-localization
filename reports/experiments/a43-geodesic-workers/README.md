# A43 geodesic source-worker diagnostic archive

This archive preserves the saved prototype edit scripts, parity-test insertion scripts, benchmark harness, raw comparison outputs, and a reconstructed source patch for review. The patch is not the exact measured source and must not be applied as though it reproduced the reported runs.

The clean 20,000-pair timing record reports measured implementation SHA-256 `1e15e477a0843c18c377dd403ea8a23753b24f7a4158145e13171e8d1eabbb69`. The prototype source was removed from the worktree before it was snapshotted. Reapplying the retained edit scripts and formatting the result produced SHA-256 `90a46b0d6b4ad3e4100393c298d771b05925386055e6bd16ad7e8409bee9e956`, which does not match the measured source hash. The reconstructed patch is retained only as a diagnostic aid. The exact measured source therefore cannot currently be reproduced from this archive.

The production worktree remains at the serial generator. The worker comparison was slower than serial and no multiprocessing API is approved for integration. Benchmark commands and input hashes appear in the A43 performance report and JSON artifacts.
