# A37 cached-driver independent smoke audit

## Scope

This audit exercised the real cache handoff from the A26 parent supervisor to its worker subprocess after the strict configuration loader fix. The driver source was pinned to `1ef881b6d761b56cdf920b5812bcc5bf5ba71422`, the scoring/runtime source to `0d34a17ed384da63936de0017937b38609921239`, and the proposal-generation source to `33fded7641a0dd93c04dd2d26f290dd99dccb90d`. No production files were changed by this audit.

## Successful process evidence

The real cache contained 1,457,546 candidate IDs. The smoke requested an explicit ordered prefix of one candidate with the outer request supervisor timeout set to 180 seconds. The supervisor exited successfully with `status=completed` in 123.884 seconds. The runtime pool started one worker, scored and committed one candidate, and reported zero failures.

The full immutable candidate plan and disposition ledger were independently streamed and checked. All 1,457,546 disposition IDs were unique and matched the candidate plan. The ledger recorded one `exact_scored` candidate and 1,457,545 `not_selected_by_explicit_prefix_profile` candidates. The manifest reports `request_complete=true` and `selected_request_complete=true`, while `all_candidates_accounted=false` and `finite_candidate_set_certified=false`. This verifies a successful bounded request and accounting for its unselected prefix suffix; it does not verify full-domain scoring, finite-set certification, or accuracy.

The driver output is preserved under `.agent-work/outputs/a37-prefix-smoke-1ef881b/`; the supervisor log is `.agent-work/outputs/a37-prefix-smoke-1ef881b.supervisor.log`. The manifest records 71.38 seconds for contract/cache load, 12.28 seconds for coordinate transform and CLI ordering, and 1.40 seconds for exact scoring. Cache materialization dominates this small-prefix smoke.

## Preserved failure cases

A first driver run at `c159704` used a five-second external deadline. It confirmed the parent-held lock and child lock bypass worked, terminated and reaped the process group, and recovered all 1,457,546 cached IDs in the interruption ledger as worker-state-unknown. That output remains under `.agent-work/outputs/a37-driver-smoke-c159704/`.

A second run at `f004601` reached scoring but failed because the driver manually constructed `SearchConfig` with an unexpected `contract_type` field. It retained the complete candidate inventory and explicitly marked the single selected ID unknown. A26 fixed the issue by loading the versioned search configuration through the strict contract reader. The failed output remains under `.agent-work/outputs/a37-prefix-smoke-f004601/`.

The successful rerun at `1ef881b` demonstrates that the strict reader and the A30 disposition API work together across the real parent/worker process boundary. The earlier failed attempts are retained and are not counted as passing runs.

## Limits

This was a bounded one-candidate execution profile over a real cached domain. It is not a full-domain run, a throughput benchmark, a GPU validation, an accuracy evaluation, or evidence of complete-coast map coverage. No held-out evaluation was performed. The partial map coverage and unresolved regions remain a release limitation for claims beyond the supported coverage.

## Integration follow-up

The coordinator reports integration `bb2ff81` contains A26 driver `1ef881b` and A30 pool `b149b7a`. A focused standalone integration run initially failed because tools were imported without an installed package context; the integration fix corrected that invocation. The coordinator reports the corrected focused run at `bb2ff81` passed 45 tests with 1 environment-gated skip in 10.17 seconds. This is coordinator-provided integration evidence, separate from the real subprocess smoke above. The skip is not hardware validation.

## Source and artifact pins

The successful process used driver commit `1ef881b6d761b56cdf920b5812bcc5bf5ba71422`, scoring/runtime commit `0d34a17ed384da63936de0017937b38609921239`, generation commit `33fded7641a0dd93c04dd2d26f290dd99dccb90d`, and cached-generation run `a26-screened-cachegen-port001-placement001-r0-10km-typesoff-v3`.

The preserved strict-config failure used driver commit `f004601a5f3d96854f64e9e78124c01df9c7854f` and the same scoring/runtime and generation commits. It failed with `SearchConfig.__init__() got an unexpected keyword argument contract_type`; A26 replaced permissive manual construction with strict versioned contract loading in `1ef881b`.

The preserved short-timeout run used driver commit `c159704785906ec4eea46715cda5d4acad652402`. Its five-second parent deadline terminated/reaped the worker process group and produced a complete interruption inventory. This run is timeout recovery evidence, not a successful scoring request.

## Final continuation gate at `1002d733b557aa94c0a7da46dc6733c654f44af3`

The clean A26 resumable checkout at `1002d733b557aa94c0a7da46dc6733c654f44af3` passed the focused integration command:

```text
python -m pytest -q tests/accuracy/test_a26_cached_finite_domain.py tests/accuracy/test_a30.py tests/accuracy/test_a30_cpu.py tests/accuracy/test_a30_screened.py tests/accuracy/test_a22.py tests/accuracy/test_a22_screened.py
68 passed, 12 skipped
```

The two real CLI attempts used generation source `33fded7641a0dd93c04dd2d26f290dd99dccb90d`, scoring source `0d34a17ed384da63936de0017937b38609921239`, and the v4 cached proposal artifact. Attempt 1 wrote `.agent-work/outputs/a37-resume-smoke-1002d733/attempt-1`. Attempt 2 wrote `.agent-work/outputs/a37-resume-smoke-1002d733/attempt-2` with `--resume-from attempt-1`. Both completed as exact one-candidate prefix requests. Attempt 2 validated and reused the parent's committed chunk and hypothesis sidecar, with identical run identity, chunk-tree, hypothesis-tree, and disposition-ledger hashes. It did not rescore the committed candidate.

Both manifests recorded 1,457,546 total plan IDs, one exact-scored candidate, 1,457,545 candidates outside the explicit one-candidate prefix, zero failed, unresolved, or unvisited IDs, and `request_complete=true`. Both correctly retained `all_candidates_accounted=false` and `finite_candidate_set_certified=false`. The result is a driver continuation check, not full-domain scoring, finite-set certification, or an accuracy evaluation.

A small direct integrity fixture against `resume_verified_chunks` accepted a valid parent, rejected an edited chunk score when the declared chunk-tree hash was stale, and rejected a parent manifest missing `candidate_dispositions_sha256`. The full focused suite also passed action/counter, strict identity, ordering, sidecar, and lineage cases. Disk remained above the 40 GiB reserve, with 42 GiB free after the two attempts. No production source was changed during this audit.

The A37 decision is GO for the separately authorized bounded exact scoring run. Map coverage remains partial; this gate does not support a complete-coast accuracy claim or held-out evaluation.
