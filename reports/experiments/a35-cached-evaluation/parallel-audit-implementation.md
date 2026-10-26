# A35 parallel audit adapter

The new evaluator uses the reviewer’s immutable-source, event-chain, checkpoint-prefix, and manifest validation helpers before opening truth. The parallel run is an interrupted 63-of-64 producer with unknown mode 48. The adapter preserves that raw status and forms a separate composite selected set from parallel modes 0–47 and 49–63 plus the audited serial mode 48.

The structural gate passed on the frozen artifacts. The serial run has 60 completed rows and 40,871 event records. The parallel run has 63 rows, 41,278 event records, and unknown mode 48. Their source bindings, scorer inputs, package and runner pins, four worker provenance records, row overlap, event overlap, and checkpoint prefixes match. Parallel mode 48 events are a prefix of the completed serial mode 48 event ledger. Serial mode 60’s 109 partial events are an exact prefix of the parallel mode 60 ledger.

The adapter rechecks both starts and the saved best hypothesis for all 64 selected modes. It reuses exact branch-result scores only for completed serial rows 0–59 after exact row and event-payload equality is established. It independently rescored branch poses for parallel modes 60–63, including the full mode-60 tail. It preserves non-exact branch results and raw mode-48 partial events in the audit record. Truth metrics run only after these checks succeed.

Tests cover source and file-hash binding, exact event payload overlap, a missing composite mode, nonminimum starts, an incorrect best hypothesis, a missing new pose, and an extra parallel-only pose that must be rescored. At this implementation checkpoint, 32 focused tests pass. Ruff, ty, and `git diff --check` pass. The structural gate was run, but the expensive scorer replay and evaluator truth read have not yet run.

The frozen parallel manifest hash is `1f032a2270517d0f2ba5f303f335de3329e9f5532806969f65bee006e6617060`; its rows hash is `34874d530ed45a1ee8da40da7ee78650dc84f2033a454e7ba967f48faabe6068`; its event hash is `59e52547471065853a1c1b35bc46f8b4393d254559317c237bb5675196f23f24`. The structural gate’s hashes and mode selection are recorded in the adapter output when the evaluator pass is authorized.
