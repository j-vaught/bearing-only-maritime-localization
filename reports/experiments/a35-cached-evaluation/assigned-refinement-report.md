# Assigned bearing refinement evaluation

This evaluator-only comparison uses the frozen development trial `development/port-001/placement-001/r0/range10000m/typesoff/nominal`. The truth remained outside all search and refinement steps. It was opened only after validating the partial finite-domain run, its A20 64-mode refinement, and the completed assigned-refinement manifest and row hashes. Candidate positions use EPSG:32618; evaluator truth uses EPSG:32619 and was transformed explicitly for geodesic errors.

The source finite-domain run is incomplete. It durably scored 47,182 candidates, recorded 82 unresolved candidates, and left 1,410,282 candidate dispositions unknown when its worker was stopped to preserve the disk reserve. A20 selected 64 modes from that partial set and completed continuous refinement. The assigned-bearing runner then completed 64 of 64 selected modes, with two starts per mode, 106 accepted exact steps among 232 attempted steps, in 57.6525 seconds. The run claims no full-domain completion or certificate.

| Selected-set stage | Best objective | Error of best-score mode | Direct truth-key assignments | Closest selected mode error | Score at closest mode |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cached source modes | 14.605335 | 853,001.682 m | 0 of 3 | 296.286 m | 16.355641 |
| A20 continuous refinement | 10.324237 | 853,439.780 m | 0 of 4 | 266.378 m | 14.859610 |
| Assigned fit from cached starts | 10.324237 | 853,439.792 m | 0 of 4 | 181.285 m | 14.777115 |
| Assigned fit from A20-refined starts | 10.324237 | 853,439.780 m | 0 of 4 | 181.279 m | 14.777115 |

All 64 selected modes were evaluated at each stage. The minimum-score candidate remained the wrong mode at approximately `(493306.84, 4243315.76)` in EPSG:32618. It scored 10.324237, had four assignments and zero direct truth-key matches, and lay 853.440 km from truth. The assigned fit did not change this selected-set score leader. Across the 64 modes, the cached start won 25 and the A20-refined start won 39.

The nearest selected mode was A20 mode 4, source candidate `cell:cell:-419:1405`, input index 34280. Its saved candidate was 296.286 m from truth with score 16.355641. A20 refinement moved it to 266.378 m and score 14.859610. Assigned fitting moved it to 181.279 m and score 14.777115. The final point was classified as water by the frozen map. It was the closest selected mode but ranked 16th by assigned objective within the selected set.

Both assigned-fit starts accepted one exact improvement and then stopped with `no_improvement`. The final assignment remained reports 0, 1, 2, and 4. Report 3's true group was present in the predicted visible groups at the final point, 1,516.31 m away. Its bearing residual was 2.1501 degrees with sigma 0.5 degrees. Matching that report and group cost 8.564, while leaving them unmatched cost 8.189, so exact global assignment omitted report 3 by 0.375 cost units. The local fit therefore stayed in a four-report association basin; it did not enter land, lose the target through missing map geometry, or reject an exact improving proposal.

At truth, the pinned scorer predicts and matches all five true groups, with objective 5.206587. That is lower than both the nearest four-report basin at 14.777115 and the selected score leader at 10.324237. We re-scored truth and both fixed selected positions with the original scoring source pinned by the run identity, commit `0d34a17`; the resulting scores matched the assigned-run scorer. The truth score comparison is therefore on the same objective and immutable measurements, catalog, map, and configuration.

A geometry-only calculation at the evaluator truth point helps explain the basin. With independent 0.5-degree bearings and the known truth group coordinates, the four groups associated by the near mode (reports 0, 1, 2, and 4) give a linearized 95% major semiaxis of 607 m and information condition number 762. Including report 3 changes those values to 145 m and 58.7. These are evaluator-only linearized information diagnostics, not a calibrated covariance or an estimator uncertainty result.

The comparison establishes a useful local behavior, not a localization correction. Assignment-conditioned fitting improves a truth-adjacent water mode but preserves its starting association set, while the true five-report assignment remains a distinct lower-score basin. The search source remains incomplete, so these 64 modes cannot establish global accuracy, completeness, or acceptance.

## Provenance

The source run identity is `3402e8cbb30e690816935541ada1472ed55a0d9ec3ea5220585bdf8e6d87a569`; its supervisor manifest is `66df01fa92009eaf4ebcb2a6d051fa8ce726b7e8e66209e0ee9930cfb110634a`. The A20 manifest and rows hashes are `1cc41596e787c990950b4d1f00043c73b4815ffaf9e2371c762bf356d3bba063` and `ac8b3fac53447ec6dbc2ee310ca7ec0e5068411449545ab41dff3e4b418fce49`. The assigned-run manifest and rows hashes are `ecde0ac0eaab7eef8569638f306e2a8c6300a78a1c54716f6ab30d6c5994ba01` and `d8e921d46b5067c1e12cacbd3eff930f5acb48c27192ed2527cef3159e3c9955`.

The assigned-fit harness commit is `e2e8b879484ca1afb73c69d081619a391575af7f`; its run pins the frozen scoring source `0d34a17ed384da63936de0017937b38609921239`. We re-scored the truth point, selected score leader, and nearest selected mode using that pinned source and verified their scores against the assigned-run output. The search configuration hash is `adef2647b728c2459c64a7f88b3c7a821f6bce8f05fb25cacd0e98dbd9cf2df0`; the map hash is `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`. The truth, support, and truth-CRS hashes are `4cce08dae52b823d94233f6c60ccb049297e4e4ec9648fb0017eae52c11d7d48`, `fcd0d0bc5afbe10125d80799d99c83ac84beccbadc90223c23800c4bf9a400de`, and `ab92cb4c3c74d31fa039ecee0cd17078a28521cdd05b181815b0e6d7728ab895`.

The full 64-mode record and proposal diagnostics are in [assigned-refinement-evaluation.json](assigned-refinement-evaluation.json). Its SHA-256 is recorded with the commit.
