# A35 coarse-seed assignment-expansion evaluation

This evaluator targets the separate coarse-seed parallel diagnostic. It verifies producer rows and event ledgers, checkpoint prefixes, mode disposition counts, frozen A20 and assigned-refinement bindings, and the original A20 hypothesis for each matching input index and candidate identity. It retains all three start alternatives and independently rescores the saved alternatives, chosen seed, and final best hypothesis with the frozen production scorer before any truth file is opened.

The accuracy output covers only durable selected-mode seeds and final best hypotheses. It preserves each mode status and branch counters, including failures, pending modes, and budget exhaustion. It does not replay every branch pose, reuse another seed-stage audit, or claim whole-domain coverage, acceptance, or a certificate.

After the producer finalizes, run the integrity gate with the A20 bundle, assigned bundle, and immutable output. The evaluator writes a compact result JSON. Truth evaluation is a separate step and is allowed only after the integrity gate and canonical rescoring pass.

The focused suite uses the existing Python 3.11 environment and passes before evaluator truth is opened.

At this preparation milestone, the producer has not been evaluated and no evaluator truth was read.
