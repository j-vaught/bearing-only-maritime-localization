# Assignment expansion diagnostic runner

The runner consumes the durable 64-mode assigned refinement artifact and its A20 cached-mode source artifact. It derives the frozen static inputs and scoring configuration from the recorded source pins, validates the artifact hashes and source references, and reconstructs the canonical scorer before any expansion branch is visited.

Each mode retains both finalized assigned-refinement alternatives. The selected start is the lower exact finalized score, with the frozen label order providing the deterministic tie break. Both alternatives are canonically re-scored and their assignments and scores must match the saved values. The selected exact hypothesis is passed to expand_assignments with ExpansionConfig(local_radius_m=1000, max_nfev=100, max_branches=5000).

The output is an immutable, unique directory containing fsynced per-mode rows, a hash-chained branch-event stream, a checkpoint, and a manifest. The callback checks the 40 GiB free-space reserve and 20 MiB output cap before and after every event. A timeout, resource failure, or branch exception is retained in the event stream and checkpoint before the run terminates. Resume is explicitly unsupported.

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

The diagnostic has not been executed in this branch.
