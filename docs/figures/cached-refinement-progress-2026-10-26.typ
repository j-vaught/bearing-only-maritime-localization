#import "@preview/lilaq:0.6.0" as lq

#set page(width: 300mm, height: 340mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-refinement-progress-2026-10-26.json")
#let modes = data.at("mode_comparisons")
#let top = data.at("top_score_winner")
#let alt = data.at("lowest_error_selected_alternative")
#let ranks = modes.map(row => row.at("source_score_rank"))
#let source-errors = modes.map(row => row.at("source_error_m"))
#let continuous-errors = modes.map(row => row.at("continuous_error_m"))
#let assigned-errors = modes.map(row => row.at("assigned_error_m"))
#let rounded(value, digits) = calc.round(value * calc.pow(10, digits)) / calc.pow(10, digits)
#let top-stages = top.at("stages")
#let alt-stages = alt.at("stages")

#align(center)[
  #text(size: 14pt, weight: "bold")[Cached-candidate refinement and assigned-fit diagnostic]
  #linebreak()
  #text(size: 9pt)[64 score-selected modes from an incomplete, partial-cache source run. Snapshot dated 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8pt, weight: "bold")[Assigned fitting improved one nearby alternative, while the selected score winner remained wrong by 853.440 km. This is not a localization fix or release-gate result.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[Selected score winner]
#text(size: 7.5pt)[The source-score-rank-3 seed became the continuous-refinement score winner. Assigned fitting retained that same score winner and its wrong four-object association.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Error at the selected score winner across stages],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 1050000),
    ylim: (0.4, 3.6),
    xaxis: (ticks: ((0, [0]), (200000, [200k]), (400000, [400k]), (600000, [600k]), (800000, [800k]), (1000000, [1,000k])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Continuous refinement]), (3, [Assigned fit])), subticks: none),
    width: 260mm,
    height: 48mm,
    lq.hbar((top-stages.at("source").at("error_m"),), (1,)),
    lq.hbar((top-stages.at("continuous").at("error_m"),), (2,)),
    lq.hbar((top-stages.at("assigned").at("error_m"),), (3,)),
  )
]
#v(1mm)

#text(size: 10pt, weight: "bold")[Lowest-error selected alternative. Not the estimate.]
#text(size: 7.5pt)[This separate mode improved from 296.286 m to 266.378 m, then to 181.279 m after assigned fitting. Its assigned objective rank is 16 of 64.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Error progression for the lowest-error selected alternative],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 350),
    ylim: (0.4, 3.6),
    xaxis: (ticks: ((0, [0]), (100, [100]), (200, [200]), (300, [300])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Continuous refinement]), (3, [Assigned fit])), subticks: none),
    width: 260mm,
    height: 42mm,
    lq.hbar((alt-stages.at("source").at("error_m"),), (1,)),
    lq.hbar((alt-stages.at("continuous").at("error_m"),), (2,)),
    lq.hbar((alt-stages.at("assigned").at("error_m"),), (3,)),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1.25fr, 1fr, 1fr, 1fr, 1.5fr, 1fr, 1fr),
    inset: 2.2pt,
    stroke: 0.5pt + black,
    [Mode], [Source rank], [Errors source → continuous → assigned (m)], [Scores source → continuous → assigned], [True-key assignments source → continuous → assigned], [Assigned score rank], [Assigned error rank],
    [Selected score winner],
    [#str(top.at("source_score_rank"))],
    [#str(rounded(top-stages.at("source").at("error_m"), 1)) → #str(rounded(top-stages.at("continuous").at("error_m"), 1)) → #str(rounded(top-stages.at("assigned").at("error_m"), 1))],
    [#str(rounded(top-stages.at("source").at("score"), 3)) → #str(rounded(top-stages.at("continuous").at("score"), 3)) → #str(rounded(top-stages.at("assigned").at("score"), 3))],
    [#str(top-stages.at("source").at("true_assignments")) / #str(top-stages.at("source").at("assignments")) → #str(top-stages.at("continuous").at("true_assignments")) / #str(top-stages.at("continuous").at("assignments")) → #str(top-stages.at("assigned").at("true_assignments")) / #str(top-stages.at("assigned").at("assignments"))],
    [#str(top.at("assigned_score_rank")) / 64],
    [#str(top.at("assigned_error_rank")) / 64],
    [Lowest-error alternative. Not estimate.],
    [#str(alt.at("source_score_rank"))],
    [#str(rounded(alt-stages.at("source").at("error_m"), 1)) → #str(rounded(alt-stages.at("continuous").at("error_m"), 1)) → #str(rounded(alt-stages.at("assigned").at("error_m"), 1))],
    [#str(rounded(alt-stages.at("source").at("score"), 3)) → #str(rounded(alt-stages.at("continuous").at("score"), 3)) → #str(rounded(alt-stages.at("assigned").at("score"), 3))],
    [#str(alt-stages.at("source").at("true_assignments")) / #str(alt-stages.at("source").at("assignments")) → #str(alt-stages.at("continuous").at("true_assignments")) / #str(alt-stages.at("continuous").at("assignments")) → #str(alt-stages.at("assigned").at("true_assignments")) / #str(alt-stages.at("assigned").at("assignments"))],
    [#str(alt.at("assigned_score_rank")) / 64],
    [#str(alt.at("assigned_error_rank")) / 64],
  )
]

#v(2mm)
#text(size: 10pt, weight: "bold")[Errors across the selected subset]
#text(size: 7.5pt)[Each marker is one of 64 score-selected modes. The horizontal axis shows the original score rank among 47,182 exact-scored hypotheses. These ranks come from an incomplete source search and are not a complete-domain ranking.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Geodesic error versus source score rank],
    xlabel: [Source score rank among exact-scored hypotheses],
    ylabel: [Geodesic error (m)],
    xlim: (0, data.at("max_source_score_rank") + 2),
    ylim: (0, data.at("plot_error_max_m")),
    xaxis: (ticks: ((1, [1]), (16, [16]), (32, [32]), (48, [48]), (64, [64]), (80, [80])), subticks: none),
    width: 260mm,
    height: 54mm,
    lq.scatter(ranks, source-errors, mark: "s"),
    lq.scatter(ranks, continuous-errors, mark: "^"),
    lq.scatter(ranks, assigned-errors, mark: "d"),
  )
]
#v(1mm)
#align(center)[#text(size: 7pt)[Series order. Square is cached source, triangle is continuous refinement, diamond is assigned fit. No legend box is used.]]

#v(1mm)
#text(size: 6.5pt)[
  Assigned fitting completed 64 modes from 128 starts in #str(rounded(data.at("assigned_run").at("runtime_s"), 4)) s, accepting #str(data.at("assigned_run").at("accepted_exact_steps")) of #str(data.at("assigned_run").at("attempted_exact_steps")) exact steps. This is a selected-mode diagnostic, not full-frame or coast-wide timing. Across the 64 rows, objective score improved in #str(data.at("summary").at("score_improved_count")) modes; final error improved in #str(data.at("summary").at("error_improved_count")) and worsened in #str(data.at("summary").at("error_worsened_count")). Median error was #str(rounded(data.at("summary").at("median_source_error_m"), 1)) m at source, #str(rounded(data.at("summary").at("median_refined_error_m"), 1)) m after continuous refinement, and #str(rounded(data.at("summary").at("median_assigned_error_m"), 1)) m after assigned fitting. The source run left #str(data.at("source_exact_hypothesis_count")) hypotheses exact-scored, #str(data.at("source_exact_unresolved_count")) unresolved, and #str(data.at("source_unknown_count")) in an unknown state at interruption. Evaluator artifact SHA-256: #data.at("source_artifact_sha256"). A20 manifest SHA-256: #data.at("a20_manifest_sha256"). Assigned manifest SHA-256: #data.at("assigned_manifest_sha256"). Evaluator source commit: #data.at("source_artifact_commit"). Truth was used only for independent evaluation. Authorship J.C. Vaught.
]
