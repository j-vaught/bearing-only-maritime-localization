# Evaluator-only comparison of truth-blind mode refinement

## Scope and bindings

This comparison evaluates the immutable cached-domain attempt at a26-cached-exact-full-1002d73 and the separate truth-blind refinement at a20-cached-refinement-v2. A20 completed its preselected 64-mode refinement before this evaluator opened truth. The evaluator verified the source attempt identity and supervisor, candidate and disposition bindings, chunk and hypothesis tree hashes, selected mode records, source shard hashes, refinement row digest, frozen package commit, and accepted score history.

The source run remained incomplete. It contains 47,182 exact-scored hypotheses, 82 unresolved candidates, and 1,410,282 candidates with unknown state after the disk-reserve interruption. A20 selected 64 spatial modes from the committed, score-ranked subset using saved eight-neighbor local minima and 100 m separation. The explicit cap was reached. This comparison covers those 64 modes only.

The refinement manifest has SHA-256 1cc41596e787c990950b4d1f00043c73b4815ffaf9e2371c762bf356d3bba063. Its 64-row JSONL has SHA-256 ac8b3fac53447ec6dbc2ee310ca7ec0e5068411449545ab41dff3e4b418fce49. The evaluator result is refinement-v2-evaluation.json, SHA-256 88290e7ac580c0cd7bd32fb7fd813e0ff7e55ac3a7aefbcca5ee295f0a0a79cd. It was produced with evaluator commit 92d54d4f489783e2dfc4e3f68d8b2cc99ba7b7b0, refinement harness commit 0c0271d17b1ea82ad14c8e14a0abc4c9cb6e84e9, and frozen scoring package commit 0d34a17ed384da63936de0017937b38609921239.

## Paired results

All 64 modes completed. Revalidation preserved each cached seed position and objective. Every refined mode had a lower objective than its seed, with a mean score decrease of 2.910. Geodesic error improved for 37 modes and worsened for 27. The median error changed from 950,060.534 m to 950,085.827 m. Across the 64 modes, source assignments totaled 201 with 4 direct truth-key matches; refined assignments totaled 210 with the same 4 direct matches. One mode contained those correct assignments, and it remained the only mode whose assignments all matched truth.

| Evaluated subset | Modes | Mean objective | Median position error | Mean error | Assignments | Direct truth-key matches |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cached seed | 64 | 20.429 | 950,060.534 m | 1,150,745.027 m | 201 | 4 |
| Refined mode | 64 | 17.519 | 950,085.827 m | 1,150,780.130 m | 210 | 4 |

The lowest refined objective was 10.324237 at candidate cell:cell:-419:1403, source score rank 3. Its refined position remained 853,439.780 m from truth and none of its four assignments matched the truth keys. The closest refined result within the selected set was 266.378 m from truth at candidate cell:cell:1594:4160, with 4 of 4 assignments correct. It began at score rank 5 and ranked 11th by refined objective within the 64 refined modes. That mode stopped at the 50-iteration limit. The closest hypothesis in the broader saved source set was 101.335 m from truth, but it was not among the 64 selected modes.

## Interpretation

Continuous refinement lowered the objective throughout the selected set, but it did not consistently move estimates closer to truth. The best-objective refined mode remained far from truth, while the closest refined mode retained a substantially worse objective and reached the iteration limit. The evidence therefore shows local objective improvement on a capped set of modes, not a corrected localization result.

The source run is still incomplete, with more than 96% of candidate dispositions unknown. The refined set is a subset selected from that incomplete run. Neither the source run nor this refinement establishes full-domain accuracy, a global certificate, or an accepted fix.

