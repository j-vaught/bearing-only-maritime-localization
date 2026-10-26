# A35 coarse-seed assignment-expansion evaluation

This evaluator targets the separate coarse-seed parallel diagnostic. It verifies producer rows and event ledgers, checkpoint prefixes, mode disposition counts, frozen A20 and assigned-refinement bindings, and the original A20 hypothesis for each matching input index and candidate identity. It retains all three start alternatives and independently rescores the saved alternatives, chosen seed, and final best hypothesis with the frozen production scorer before any truth file is opened.

The accuracy output covers only durable selected-mode seeds and final best hypotheses. It preserves each mode status and branch counters, including failures, pending modes, and budget exhaustion. It does not replay every branch pose, reuse another seed-stage audit, or claim whole-domain coverage, acceptance, or a certificate.

After the producer finalizes, run the integrity gate with the A20 bundle, assigned bundle, and immutable output. The evaluator writes a compact result JSON. Truth evaluation is a separate step and is allowed only after the integrity gate and canonical rescoring pass.

The focused suite uses the existing Python 3.11 environment and passes before evaluator truth is opened.

At this preparation milestone, the producer has not been evaluated and no evaluator truth was read.


## Attempt 1 preparation failure

The immutable first attempt stopped before any localization mode was dispatched. The producer failed while reading the original A20 mode record because the runner treated a typed ModeRow as a dictionary. The manifest and checkpoint agree on failed status and the same error. Both row and event ledgers are empty, with SHA-256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855. All 64 modes remain unknown; no modes completed, and no truth was read.

The independent failure record is parallel-coarse-attempt-1-failure.json. Its manifest SHA-256 is 7e09fe13d71d3ebb5648286a18afb244a906dbb95fcaf341e7ab7aa0c5658421, and its checkpoint SHA-256 is 361c68b5a3988715069d847a5b1bd575e5e9640dbad0fe307ed189fcc11704aa. This record reports accuracy as not evaluated and does not infer any localization result.

The corrected producer attempt is isolated under coarse-attempt-2. Its rows, events, hypotheses, and provenance will be validated independently; none of attempt 1's empty ledgers are reused as evidence for that run.
