# Assignment expansion diagnostic runner

The runner consumes the durable 64-mode assigned refinement artifact and its A20 cached-mode source artifact. It derives the frozen static inputs and scoring configuration from the recorded source pins, validates the artifact hashes and source references, and reconstructs the canonical scorer before any expansion branch is visited.

Each mode retains both finalized assigned-refinement alternatives. The selected start is the lower exact finalized score, with the frozen label order providing the deterministic tie break. Both alternatives are canonically re-scored and their assignments and scores must match the saved values. The selected exact hypothesis is passed to expand_assignments with ExpansionConfig(local_radius_m=1000, max_nfev=100, max_branches=5000).

The output is an immutable, unique directory containing fsynced per-mode rows, a hash-chained branch-event stream, a checkpoint, and a manifest. The callback checks the 40 GiB free-space reserve and 50 MiB output cap before and after every event. A timeout, resource failure, or branch exception is retained in the event stream and checkpoint before the run terminates. Resume is explicitly unsupported.

The manifest reports ranked selected-mode results with processed and pending counts while retaining decision_status=ambiguous, search_status=budget_exhausted, certificate_scope=none, and full_domain_complete=false. The source search therefore remains incomplete and no truth or evaluator input is used.

A read-only preflight under the canonical lock inspected the normal candidate pools without fitting expansion branches. Across 64 modes it found 14,473 pool records, 14,257 eligible records, 104 unmatched-report slots, and 22,000 single-addition candidate branches. Per-mode eligible records ranged from 3 to 1,061 and candidate branches ranged from 0 to 3,183. No nonvisual or unsupported-model records were counted.

The proposed execution command is below. It must be approved by the coordinator after the prerequisite module commit and other locked jobs finish.

~~~text
PYTHONPATH=/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/assignment-expansion-runner \
/home/jvaught2/projects/bearing-only-maritime-localization/.venv/bin/python \
tools/run_assignment_expansion_diagnostic.py \
--assigned /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/assigned-bearing-refinement-diagnostic-v1 \
--a20 /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a20-cached-refinement-v2 \
--output /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/assignment-expansion-diagnostic-v1 \
--lock /home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/benchmark.lock \
--timeout-s 1800
~~~

The first approved launch was rejected before creating an output directory because the
startup check measured the shared output parent against the per-run cap. Its captured
failure was `OSError: diagnostic output exceeded 50 MiB`; it produced no branch records.
The corrected run used harness commit
6f7cdf4354fddbc7237625bde2d82763362068c8, with the expansion module unchanged from
the approved source, and completed 60 of 64 modes before the 1,800 s whole-run
deadline. The final status is interrupted with four pending modes, 40,871 durable
hash-chained branch events, 60 durable rows, and 22,055,971 output bytes. The event
stream verified with zero chain or event-hash errors. The output directory remains
partial by design, with decision_status=ambiguous, search_status=budget_exhausted,
certificate_scope=none, full_domain_complete=false, and truth_used=false.

The durable output is at
/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/assignment-expansion-diagnostic-v1.
Its manifest records rows_sha256=2bb8a9f2f3e0e46f4456c7ead57efda5afab6776465552156ea863a8574a7c7c,
events_sha256=a92acc79294bd045605f5bcce9e76e26ab7ccddc03ccba1ace0df69eb982b7bf,
and event_digest=70a682e2766f18fb6fb83819ed945e078b2958cf3ac465ecdb990f79ed87f3ae.
The run used no truth or evaluator inputs. The assigned input root was
/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/assigned-bearing-refinement-diagnostic-v1,
and the A20 input root was
/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a20-cached-refinement-v2.
The loaded package source was
/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/worktrees/assignment-expansion-runner/bearing_localization/__init__.py
at package commit 6f7cdf4354fddbc7237625bde2d82763362068c8. The assignment expansion
module hash was 4dd078b7298d29453037b8f7cb91ec8a5903f65002e350f449dd1cfe5c80f93c,
and the runner hash was 8b5bc103cad158434424fcb46e2126a74aa6c10ce4b7d4d90348ff2a88d62f19.
The final checkpoint was last written during the interrupted mode and remains an
in-progress checkpoint; the manifest and fully fsynced rows and event stream are the
final durable records for the partial run.
