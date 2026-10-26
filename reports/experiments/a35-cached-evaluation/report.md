# Evaluator-only report for the interrupted cached-domain attempt

**Scope and bindings.**

This report evaluates the immutable attempt at `/home/jvaught2/projects/bearing-only-maritime-localization/.agent-work/outputs/a26-cached-exact-full-1002d73`. The evaluator used commit `0daf3d0`, generator source `33fded7641a0dd93c04dd2d26f290dd99dccb90d`, scoring source `0d34a17ed384da63936de0017937b38609921239`, and the frozen development trial `development/port-001/placement-001/r0/range10000m/typesoff/nominal`. The candidate coordinates use EPSG:32618 and the evaluator truth uses EPSG:32619. Position errors were computed geodesically after transforming both coordinates to WGS84.

The evaluator verified the attempt identity, candidate plan and inventory, chunk and hypothesis trees, disposition ledger, static input hashes, and evaluator support. The five-group trial is marked supported with valid reference water. The supervisor manifest SHA-256 is `66df01fa92009eaf4ebcb2a6d051fa8ce726b7e8e66209e0ee9930cfb110634a`; the disposition ledger SHA-256 is `cdd81caa5df87dc3a7300880d833c7d200d5a5bc7c137033db660d3e5e251268`. The machine-readable evaluation is `evaluation.json`, and its size is 277,341 bytes.

**Attempt completeness.**

The cache contains 1,457,546 candidates. Durable chunks cover 47,264 candidates, including 47,182 exact-scored hypotheses and 82 unresolved candidates. Zero candidate IDs are marked failed. The remaining 1,410,282 candidate dispositions are unknown after interruption. Thus 3.237% of the domain has exact scores, 0.0056% is unresolved, and 96.757% remains unvisited or unknown. The final hypothesis directory also contains a trailing sidecar for chunk 1477 with 32 hypothesis rows but no durable chunk record. The evaluator validated that these rows belong to candidates marked unknown, excluded them from the rankings, and retained their count in the output.

The supervisor status is `worker_failed` with worker return code `-15`, but the operator stop record identifies a deliberate disk-reserve intervention. At the stop check, 43,282,800,640 bytes were free, 333,127,680 bytes above the 40 GiB reserve floor. The worker process group received SIGTERM, exited about two seconds later, and the outer supervisor stayed alive to write the manifests and ledgers. The measured request duration was 859.992 seconds. The exact signal second was not captured. This was not an algorithm crash or a planned timeout.

**Best-so-far hypotheses.**

The lowest saved objective is 14.6053352926 at candidate `cell:cell:-419:1405`, global input index 46031, with position (493335.417, 4243832.337) m in EPSG:32618. Its evaluator-only geodesic error is 853,001.682 m. It has three assignments and zero direct matches to truth catalog keys. Its score components are matched cost 1.833127, clutter cost 12.772208, and missed cost 0.

The nearest saved exact-scored candidate is `cell:cell:1594:4161`, global input index 43382, at score rank 12. Its error is 101.335 m and its score is 17.8491667409. Two of its four assignments match the truth catalog keys. The score-rank-5 candidate `cell:cell:1594:4160` is 296.286 m from truth and all four of its assignments match truth catalog keys, at score 16.3556411477.

Across all 47,182 durable exact-scored hypotheses, 6 of 88,507 assignments match the truth catalog key for the corresponding report. Two candidates have any direct truth-key match, and one candidate has all of its assignments correct. These are evaluator-only association comparisons against the saved truth and do not affect search ranking.

**Interpretation.**

The best saved objective is far from truth, while a candidate 101.335 m from truth appears at objective rank 12. The run is incomplete and leaves 96.757% of its domain unvisited or unknown, so this result is a best-so-far observation rather than a full-domain accuracy result. It establishes no global optimality or acceptance certificate. No search settings or candidate rankings were changed using truth.

