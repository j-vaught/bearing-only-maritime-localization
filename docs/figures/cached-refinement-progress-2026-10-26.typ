#import "@preview/lilaq:0.6.0" as lq

#set page(width: 300mm, height: 340mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-refinement-progress-2026-10-26.json")
#let modes = data.at("mode_comparisons")
#let expansion-modes = data.at("expanded_mode_comparisons")
#let prior-winner = data.at("top_score_winner")
#let expansion-winner = data.at("assignment_expansion_winner")
#let low-error = data.at("lowest_error_selected_alternative")
#let prior-stages = prior-winner.at("stages")
#let winner-stages = expansion-winner.at("seed")
#let expanded-final = expansion-winner.at("expanded")
#let ranks = modes.map(row => row.at("source_score_rank"))
#let source-errors = modes.map(row => row.at("source_error_m"))
#let continuous-errors = modes.map(row => row.at("continuous_error_m"))
#let assigned-errors = modes.map(row => row.at("assigned_error_m"))
#let expanded-ranks = expansion-modes.map(row => row.at("source_score_rank"))
#let expanded-errors = expansion-modes.map(row => row.at("expanded_error_m"))
#let rounded(value, digits) = calc.round(value * calc.pow(10, digits)) / calc.pow(10, digits)

#align(center)[
  #text(size: 14pt, weight: "bold")[Cached refinement and single-addition expansion diagnostic]
  #linebreak()
  #text(size: 9pt)[Development diagnostic. 64 modes were score-selected from an incomplete partial-cache run. Snapshot dated 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8.5pt, weight: "bold")[Provisional expansion leader. 11.127 m and 5/5 correct assignments among 60 completed modes. Four modes remain pending. Full branch replay is incomplete. This is not an accepted fix or global certificate.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[Earlier score winner remains wrong]
#text(size: 7.5pt)[The source-score-rank-3 mode was the continuous and assigned-fit score winner. Single-addition expansion left it unchanged; it remains rank 2 among the 60 completed expansion modes, with 0 of 4 direct truth-key assignments.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Geodesic error at the earlier score winner across four stages],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 1050000),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (200000, [200k]), (400000, [400k]), (600000, [600k]), (800000, [800k]), (1000000, [1,000k])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Continuous refinement]), (3, [Assigned fit]), (4, [Single-addition])), subticks: none),
    width: 260mm,
    height: 46mm,
    lq.hbar((prior-stages.at("source").at("error_m"),), (1,)),
    lq.hbar((prior-stages.at("continuous").at("error_m"),), (2,)),
    lq.hbar((prior-stages.at("assigned").at("error_m"),), (3,)),
    lq.hbar((data.at("expanded_prior_score_winner").at("expanded").at("geodesic_error_m"),), (4,)),
  )
]
#v(2mm)

#text(size: 10pt, weight: "bold")[Provisional single-addition leader among completed modes]
#text(size: 7.5pt)[This mode was the lowest-error selected alternative before expansion. Adding the fifth matched feature moved it from 181.279 m to 11.127 m and from rank 16 to rank 1 by objective among the 60 completed modes.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Local error progression for the provisional expansion leader],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 350),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (100, [100]), (200, [200]), (300, [300])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Continuous refinement]), (3, [Assigned fit]), (4, [Single-addition])), subticks: none),
    width: 260mm,
    height: 48mm,
    lq.hbar((low-error.at("stages").at("source").at("error_m"),), (1,)),
    lq.hbar((low-error.at("stages").at("continuous").at("error_m"),), (2,)),
    lq.hbar((low-error.at("stages").at("assigned").at("error_m"),), (3,)),
    lq.hbar((expanded-final.at("geodesic_error_m"),), (4,)),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1.25fr, 0.65fr, 1.8fr, 1.8fr, 1.3fr, 1fr, 1fr),
    inset: 2.1pt,
    stroke: 0.5pt + black,
    [Mode], [Source rank], [Errors source → continuous → assigned → expanded (m)], [Scores source → continuous → assigned → expanded], [Correct assignments at each stage], [Expansion score rank], [Expansion error rank],
    [Earlier score winner],
    [#str(prior-winner.at("source_score_rank"))],
    [#str(rounded(prior-stages.at("source").at("error_m"), 1)) → #str(rounded(prior-stages.at("continuous").at("error_m"), 1)) → #str(rounded(prior-stages.at("assigned").at("error_m"), 1)) → #str(rounded(data.at("expanded_prior_score_winner").at("expanded").at("geodesic_error_m"), 1))],
    [#str(rounded(prior-stages.at("source").at("score"), 3)) → #str(rounded(prior-stages.at("continuous").at("score"), 3)) → #str(rounded(prior-stages.at("assigned").at("score"), 3)) → #str(rounded(data.at("expanded_prior_score_winner").at("expanded").at("score"), 3))],
    [0/4 → 0/4 → 0/4 → 0/4],
    [#str(data.at("expanded_prior_score_winner").at("expanded_score_rank_within_completed_modes")) / 60],
    [#str(data.at("expanded_prior_score_winner").at("expanded_error_rank_within_completed_modes")) / 60],
    [Provisional expansion leader],
    [#str(expansion-winner.at("source_score_rank"))],
    [#str(rounded(low-error.at("stages").at("source").at("error_m"), 1)) → #str(rounded(low-error.at("stages").at("continuous").at("error_m"), 1)) → #str(rounded(low-error.at("stages").at("assigned").at("error_m"), 1)) → #str(rounded(expanded-final.at("geodesic_error_m"), 3))],
    [#str(rounded(low-error.at("stages").at("source").at("score"), 3)) → #str(rounded(low-error.at("stages").at("continuous").at("score"), 3)) → #str(rounded(low-error.at("stages").at("assigned").at("score"), 3)) → #str(rounded(expanded-final.at("score"), 3))],
    [4/4 → 4/4 → 4/4 → 5/5],
    [#str(expansion-winner.at("expanded_score_rank_within_completed_modes")) / 60],
    [#str(expansion-winner.at("expanded_error_rank_within_completed_modes")) / 60],
  )
]

#v(2mm)
#text(size: 10pt, weight: "bold")[Errors across the selected subset]
#text(size: 7.5pt)[The first three marker series include all 64 score-selected modes. The single-addition series includes only 60 completed mode rows. Four modes remain pending; expansion ranks are within the completed subset only.]
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
    height: 48mm,
    lq.scatter(ranks, source-errors, mark: "s"),
    lq.scatter(ranks, continuous-errors, mark: "^"),
    lq.scatter(ranks, assigned-errors, mark: "d"),
    lq.scatter(expanded-ranks, expanded-errors, mark: "x"),
  )
]
#v(1mm)
#align(center)[#text(size: 7pt)[Series order. Square is cached source, triangle is continuous refinement, diamond is assigned fit, and cross is single-addition expansion. No legend box is used.]]

#v(1mm)
#text(size: 6.3pt)[
  The assigned-fit diagnostic used 57.6525 s for 64 modes and 128 starts. The provisional single-addition pass took #str(rounded(data.at("assignment_expansion_summary").at("serial_runtime_s"), 6)) s serially, covering #str(data.at("assignment_expansion_summary").at("completed_modes")) of 64 modes; four are pending and full branch replay is incomplete. These are independent stage diagnostics, not end-to-end or per-frame latency. The score winner after expansion has 5/5 true assignments and 11.127 m geodesic error within the completed subset, while the prior wrong winner remains 853.440 km away. This single provisional result does not establish an accuracy rate, acceptance, global ranking, or certificate. The source search left #str(data.at("source_exact_hypothesis_count")) hypotheses exact-scored, #str(data.at("source_exact_unresolved_count")) unresolved, and #str(data.at("source_unknown_count")) in an unknown state at interruption. Assignment-expansion evaluator artifact SHA-256: #data.at("assignment_expansion_artifact").at("sha256"). Assigned-fit evaluator artifact SHA-256: #data.at("source_artifact_sha256"). Expansion manifest SHA-256: #data.at("assignment_expansion_artifact").at("expansion_manifest_sha256"). The evaluator used truth only for independent error and assignment checks. Authorship J.C. Vaught.
]
