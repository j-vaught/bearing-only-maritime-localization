#import "@preview/lilaq:0.6.0" as lq

#set page(width: 300mm, height: 380mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-refinement-progress-2026-10-26.json")
#let modes = data.at("mode_comparisons")
#let expansion-modes = data.at("expanded_mode_comparisons")
#let actual = data.at("actual_stage_winners")
#let actual-stages = actual.at("stages")
#let low-error = data.at("lowest_error_selected_alternative")
#let low-stages = low-error.at("stages")
#let ranks = modes.map(row => row.at("source_score_rank"))
#let source-errors = modes.map(row => row.at("source_error_m"))
#let continuous-errors = modes.map(row => row.at("continuous_error_m"))
#let assigned-errors = modes.map(row => row.at("assigned_error_m"))
#let expanded-ranks = expansion-modes.map(row => row.at("source_score_rank"))
#let expanded-errors = expansion-modes.map(row => row.at("expanded_error_m"))
#let winner-errors = actual-stages.map(row => row.at("result").at("error_m"))
#let cached-result = actual-stages.at(0).at("result")
#let pattern-result = actual-stages.at(1).at("result")
#let assigned-result = actual-stages.at(2).at("result")
#let expansion-result = actual-stages.at(3).at("result")
#let carried = actual.at("prior_wrong_mode_after_expansion")
#let rounded(value, digits) = calc.round(value * calc.pow(10, digits)) / calc.pow(10, digits)

#align(center)[
  #text(size: 14pt, weight: "bold")[Cached refinement and single-addition expansion diagnostic]
  #linebreak()
  #text(size: 9pt)[Development diagnostic. 64 modes were score-selected from an incomplete partial-cache run. Snapshot dated 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8.5pt, weight: "bold")[The stage score winner moved from 853.002 km to 11.127 m. The last result leads the 60 completed modes; four remain pending, so the selected set is incomplete.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[Actual minimum-score estimate at each stage]
#text(size: 7.5pt)[Each point is the minimum-objective returned estimate for that stage; the winning mode can change between stages. The vertical axis is logarithmic so the 11.127 m expansion result remains legible beside the earlier 853 km results. Error uses evaluator truth after each estimate was produced.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Geodesic error of the actual stage score winner],
    xlabel: [Stage winner selected independently at each stage],
    ylabel: [Geodesic location error (m, logarithmic scale)],
    xlim: (0.5, 4.5),
    ylim: (1, 1000000),
    yscale: "log",
    xaxis: (ticks: ((1, [Cached exact source]), (2, [Pattern refinement]), (3, [Assigned fit]), (4, [Single-addition expansion])), subticks: none),
    yaxis: (ticks: ((10, [10]), (100, [100]), (1000, [1,000]), (10000, [10,000]), (100000, [100,000]), (1000000, [1,000,000])), subticks: none),
    width: 260mm,
    height: 66mm,
    lq.scatter((1, 2, 3, 4), winner-errors, mark: "s"),
  )
]
#v(2mm)

#text(size: 10pt, weight: "bold")[Single-addition leader. Local error progression for the same selected mode.]
#text(size: 7.5pt)[This was the lowest-error selected alternative before expansion. The audited branch replay moved from 296.286 m to 266.378 m, then to 181.279 m, and finally to 11.127 m after adding the fifth assignment. It ranks 1 of 60 completed modes by expansion objective, with four modes pending.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Local error progression for the audited expansion leader],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 350),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (100, [100]), (200, [200]), (300, [300])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Pattern refinement]), (3, [Assigned fit]), (4, [Single-addition])), subticks: none),
    width: 260mm,
    height: 45mm,
    lq.hbar((low-stages.at("source").at("error_m"),), (1,)),
    lq.hbar((low-stages.at("continuous").at("error_m"),), (2,)),
    lq.hbar((low-stages.at("assigned").at("error_m"),), (3,)),
    lq.hbar((data.at("assignment_expansion_winner").at("expanded").at("geodesic_error_m"),), (4,)),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1.2fr, 0.55fr, 0.75fr, 1.1fr, 0.7fr, 0.8fr, 0.65fr),
    inset: 2pt,
    stroke: 0.5pt + black,
    [Stage result], [Mode index], [Source rank], [Score], [Error (m)], [Correct assignments], [Stage score rank],
    [Cached exact source], [#str(cached-result.at("mode_index"))], [#str(cached-result.at("source_score_rank"))], [#str(rounded(cached-result.at("score"), 6))], [#str(rounded(cached-result.at("error_m"), 3))], [#str(cached-result.at("correct_assignments")) / #str(cached-result.at("assignment_count"))], [#str(cached-result.at("score_rank")) / #str(cached-result.at("rank_denominator"))],
    [Pattern refinement], [#str(pattern-result.at("mode_index"))], [#str(pattern-result.at("source_score_rank"))], [#str(rounded(pattern-result.at("score"), 6))], [#str(rounded(pattern-result.at("error_m"), 3))], [#str(pattern-result.at("correct_assignments")) / #str(pattern-result.at("assignment_count"))], [#str(pattern-result.at("score_rank")) / #str(pattern-result.at("rank_denominator"))],
    [Assigned fit], [#str(assigned-result.at("mode_index"))], [#str(assigned-result.at("source_score_rank"))], [#str(rounded(assigned-result.at("score"), 6))], [#str(rounded(assigned-result.at("error_m"), 3))], [#str(assigned-result.at("correct_assignments")) / #str(assigned-result.at("assignment_count"))], [#str(assigned-result.at("score_rank")) / #str(assigned-result.at("rank_denominator"))],
    [Single-addition expansion], [#str(expansion-result.at("mode_index"))], [#str(expansion-result.at("source_score_rank"))], [#str(rounded(expansion-result.at("score"), 6))], [#str(rounded(expansion-result.at("error_m"), 3))], [#str(expansion-result.at("correct_assignments")) / #str(expansion-result.at("assignment_count"))], [#str(expansion-result.at("score_rank")) / #str(expansion-result.at("rank_denominator"))],
    [Prior wrong mode after expansion], [#str(carried.at("mode_index"))], [#str(carried.at("source_score_rank"))], [#str(rounded(carried.at("score"), 6))], [#str(rounded(carried.at("error_m"), 3))], [#str(carried.at("correct_assignments")) / #str(carried.at("assignment_count"))], [#str(carried.at("score_rank")) / #str(carried.at("rank_denominator"))],
  )
]

#v(2mm)
#text(size: 10pt, weight: "bold")[Errors across the selected subset]
#text(size: 7.5pt)[The first three marker series include all 64 score-selected modes. The single-addition series includes 60 completed mode rows. Four modes remain pending; expansion ranks are within the completed subset only.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Geodesic error versus original source score rank],
    xlabel: [Source score rank among exact-scored hypotheses],
    ylabel: [Geodesic error (m)],
    xlim: (0, data.at("max_source_score_rank") + 2),
    ylim: (0, data.at("plot_error_max_m")),
    xaxis: (ticks: ((1, [1]), (16, [16]), (32, [32]), (48, [48]), (64, [64]), (80, [80])), subticks: none),
    width: 260mm,
    height: 44mm,
    lq.scatter(ranks, source-errors, mark: "s"),
    lq.scatter(ranks, continuous-errors, mark: "^"),
    lq.scatter(ranks, assigned-errors, mark: "d"),
    lq.scatter(expanded-ranks, expanded-errors, mark: "x"),
  )
]
#v(1mm)
#align(center)[#text(size: 7pt)[Series order. Square is cached source, triangle is pattern refinement, diamond is assigned fit, and cross is single-addition expansion. No legend box is used.]]

#v(1mm)
#text(size: 6.2pt)[
  The assigned-fit diagnostic used 57.6525 s for 64 modes and 128 starts. The single-addition pass took #str(rounded(data.at("assignment_expansion_summary").at("serial_runtime_s"), 6)) s serially, covering #str(data.at("assignment_expansion_summary").at("completed_modes")) of 64 modes; four are pending and full branch-result replay is independently audited for this completed prefix. These are independent stage diagnostics, not end-to-end or per-frame latency. The source search left #str(data.at("source_exact_hypothesis_count")) hypotheses exact-scored, #str(data.at("source_exact_unresolved_count")) unresolved, and #str(data.at("source_unknown_count")) in an unknown state at interruption. Cached-refinement evaluator SHA-256: #actual.at("source_artifact").at("sha256"). Assigned-fit evaluator SHA-256: #actual.at("assigned_artifact").at("sha256"). Assignment-expansion full-audit evaluator SHA-256: #actual.at("expansion_artifact").at("sha256"). The audit reconciled 20,435 branch-result events: 12,204 exact-rescored poses, 8,225 invalid-water outcomes, and 6 solver-limit outcomes without an exact score. Four selected modes remain pending; the run is interrupted, with no accepted-fix claim, no certificate scope, and no full-domain completion. The preceding provisional artifact SHA-256 is #actual.at("pre_audit_provisional_expansion_artifact").at("sha256"). Authorship J.C. Vaught.
]
