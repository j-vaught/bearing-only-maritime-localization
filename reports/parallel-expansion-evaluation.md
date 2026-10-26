# Independent parallel expansion evaluation review

The evaluator in `tools/evaluate_parallel_assignment_expansion.py` validates the frozen serial artifact against the future four-worker artifact without opening truth, support, validation, or held-out inputs. It binds package and harness commits, assignment-expansion source hash, frozen scoring source hash, assigned and A20 manifest and row hashes, source pins, and input paths. It requires four unique worker provenance records and verifies each worker package and source hash.

The evaluator requires the serial artifact to remain the durable 60-mode prefix and the parallel artifact to contain all 64 modes. It independently chooses each retained start by the minimum canonical start score, checks the returned seed, compares mode outcomes exactly after removing timing fields, and requires returned best hypotheses and all non-timing fields to agree. It verifies the global event hash chains, mode and branch indices, start/result pairing, per-mode branch inventory, complete mode inventories, and exact branch payload agreement for the serial overlap. Mode 60 is treated as the unfinished serial prefix and may contain additional completed work in the parallel artifact.

An optional full audit artifact is reusable only when it declares complete branch rescoring, matched raw branch payloads, and exact assignment-expansion and event hashes. Without those fields, the evaluator emits explicit provisional status and a rescore-missing reason. It never turns provisional evidence into a certificate.

The adversarial test suite has eight tests. One valid fixture passes with a 60-mode serial prefix, a globally interleaved 64-mode parallel ledger, and a partial mode-60 serial prefix. Seven corrupted fixtures are rejected for mode-order corruption, missing events, duplicate events, worker hash mismatch, branch-payload mismatch, a shortened serial prefix, and source-pin mismatch. Ruff, ty, and diff checks pass. The suite passes in 0.11 seconds.

No real parallel output evaluation was launched. The evaluator is ready for the authorized post-run comparison; the expected status is provisional until an exact full branch audit is supplied.
