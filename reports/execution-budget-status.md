# Explicit execution-budget interruption reasons

The localization result previously required every exhausted run to consume its entire integer candidate budget. A request deadline or assignment-branch limit can interrupt work after a different number of candidates, so that restriction could either fabricate candidate counts or incorrectly describe the overall request as completed.

The optional budget_exhaustion_reason field distinguishes candidate_limit, request_deadline, and assignment_branch_limit. Existing results without a reason retain the original fully consumed candidate-budget rule. Non-candidate interruptions preserve actual candidate counters, require budget_exhausted status, cannot accept a hypothesis, and carry no certificate. An interruption before candidate planning may leave both counters absent.

The serializer omits the optional field when unused, preserving existing result payload shapes. Existing readers in this implementation accept old documents unchanged. Readers from earlier revisions reject a non-null new field under their strict unknown-field policy. Version-one inputs and outputs remain unchanged.

The focused tests cover partial and zero candidate use, exhausted candidate budgets, absent plans, JSON round trips, legacy document compatibility, unknown fields, invalid counts, accepted-fix rejection, and certificate rejection. This change repairs reporting only. It does not increase any execution budget, alter the objective, or change acceptance thresholds.

J.C. Vaught
