# Cached finite-domain preparation

## Purpose

This record binds the completed development cache build to its source and inputs before cached scoring. The cache build is a preparation stage. It is not a localization accuracy result, and its one-candidate estimate is budget-limited.

## Frozen case and source

The case is development, port-001, placement-001, realization r0, nominal sensor condition, 10 km maximum range, and type labels disabled. Search receives the five-measurement set, the full 66,668-record coast catalog, the full declared map domain, and the frozen scope manifest. The runtime receives no truth input.

The cache generator source is `33fded7641a0dd93c04dd2d26f290dd99dccb90d`. The cache-driver source is `027badca0f9c129d36bdc1588f80db2a38f247c4`. Cache context SHA-256 is `3e450d04fd82d4bec5c5786d9e49960d51a0dab4615e7237b55bc493251aea44`. The cache tree SHA-256 is `d6154c18349842d22ca1cd5eaf2d022a6ac67004eebc9e35c5c59d03ad767e72`, with 126,043,477 bytes.

The frozen input SHA-256 values are measurements `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1`, catalog `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`, map `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`, and scope manifest `d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1`.

## Generation result

The generator processed all 333,340 source pairs and emitted 1,457,546 candidate cells. It examined 83,001,660 geodesic samples. No source pair or geodesic sample failed, and no generation budget was exhausted. The cache records 66,668 witness keys and 24,328,107 witness edges. The maximum solver residual was (5.010917902836809\times10^{-8}\) degrees against a (10^{-7}\)-degree tolerance.

Generation took 351.697 seconds, cache publication took 49.876 seconds, and the serialized request took 435.181 seconds. The external one-shot wall time was 437.944 seconds, including 12.608 seconds of session startup. Exact scoring was limited to one candidate and refinement was disabled. The emitted estimate therefore reports `budget_exhausted`; it must not be used as an accuracy outcome.

## Reproduction artifacts

The adjacent `cache-build` directory contains the original run manifest, telemetry, estimate, configuration, standard output, and standard error. The run manifest records the exact command arguments, source hashes, input hashes, cache path, byte size, and output hashes. The manifest SHA-256 is `13bd1e6c6cc38ba1a35ad5b816b86452b49038dc77cbba770994ecfacf088193`; telemetry SHA-256 is `dae95503bbc2dfa9a133f1a4602e0ce57eaac2252e33f8ca60113862dc95a971`; estimate SHA-256 is `ca503ebcbb2453a7d510992d20a1ce3ecec0463bad215fd15b7008b1209537f4`; and configuration SHA-256 is `a2a306c545b05173105f5c2c7409e603d20497e8193e01c3b773321b54f1675b`.

The cache payload itself remains in the server output tree rather than this tracked evidence directory. Its manifest and telemetry are tracked here so the later scoring run can be tied back to the exact cache-generation event without copying the 126 MB cache.
