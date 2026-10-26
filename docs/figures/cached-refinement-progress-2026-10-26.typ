#import "@preview/lilaq:0.6.0" as lq

#set page(width: 300mm, height: 270mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-refinement-progress-2026-10-26.json")
#let modes = data.at("mode_comparisons")
#let top = data.at("top_refined_mode")
#let alt = data.at("lowest_error_selected_alternative")
#let ranks = modes.map(row => row.at("source_score_rank"))
#let source-errors = modes.map(row => row.at("source_error_m"))
#let refined-errors = modes.map(row => row.at("refined_error_m"))
#let rounded(value, digits) = calc.round(value * calc.pow(10, digits)) / calc.pow(10, digits)

#align(center)[
  #text(size: 14pt, weight: "bold")[Cached-candidate refinement diagnostic]
  #linebreak()
  #text(size: 9pt)[64 score-selected modes from an incomplete, partial-cache source run. Snapshot dated 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8pt, weight: "bold")[The refined score winner remains wrong by 853.4 km. This diagnostic does not establish a localization fix or any release gate.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[Top result by refined score]
#text(size: 7.5pt)[The two bars track the refined score winner from its source seed to its refined result; this seed was not the source run global winner. The lowest-error alternative is tabulated separately and is not the estimate.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Error at refined winner source seed and after refinement],
    xlabel: [Geodesic location error (m)],
    xlim: (0, 1050000),
    ylim: (0.4, 2.6),
    xaxis: (ticks: ((0, [0]), (200000, [200k]), (400000, [400k]), (600000, [600k]), (800000, [800k]), (1000000, [1,000k])), subticks: none),
    yaxis: (ticks: ((1, [Refined winner source seed]), (2, [Refined winner])), subticks: none),
    width: 260mm,
    height: 52mm,
    lq.hbar((top.at("source_error_m"),), (1,)),
    lq.hbar((top.at("refined_error_m"),), (2,)),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1.35fr, 0.7fr, 0.8fr, 1fr, 1fr, 1.2fr, 0.9fr),
    inset: 2.5pt,
    stroke: 0.5pt + black,
    [Mode role], [Source rank], [Refined rank], [Source score], [Refined score], [Source to refined error (m)], [True assignments],
    [Score winner],
    [#str(top.at("source_score_rank"))],
    [#str(top.at("refined_score_rank_within_selected"))],
    [#str(rounded(top.at("source_score"), 3))],
    [#str(rounded(top.at("refined_score"), 3))],
    [#str(rounded(top.at("source_error_m"), 1)) → #str(rounded(top.at("refined_error_m"), 1))],
    [#str(top.at("refined_correct_assignments")) / #str(top.at("refined_assignment_count"))],
    [Lowest-error alternative. Not estimate.],
    [#str(alt.at("source_score_rank"))],
    [#str(alt.at("refined_score_rank_within_selected"))],
    [#str(rounded(alt.at("source_score"), 3))],
    [#str(rounded(alt.at("refined_score"), 3))],
    [#str(rounded(alt.at("source_error_m"), 1)) → #str(rounded(alt.at("refined_error_m"), 1))],
    [#str(alt.at("refined_correct_assignments")) / #str(alt.at("refined_assignment_count"))],
  )
]

#v(3mm)
#text(size: 10pt, weight: "bold")[Score-ranked alternatives in the selected subset]
#text(size: 7.5pt)[The horizontal axis is source objective rank among 47,182 exact-scored hypotheses. The 64 displayed modes were selected by search score from that incomplete run, not a complete search ranking. Paired markers show each mode before and after refinement.]
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
    height: 66mm,
    lq.scatter(ranks, source-errors, mark: "s"),
    lq.scatter(ranks, refined-errors, mark: "^"),
    lq.scatter((alt.at("source_score_rank"),), (alt.at("refined_error_m"),), mark: "d"),
  )
]
#v(1mm)
#align(center)[#text(size: 7pt)[Default series order and marker key. First square is source, second triangle is refined, third diamond is the lowest-error alternative, which is not the score-selected estimate.]]

#v(2mm)
#text(size: 6.5pt)[
  Refinement lowered objective score in #str(data.at("summary").at("score_improved_count")) / #str(data.at("selected_count")) modes; location error improved in #str(data.at("summary").at("error_improved_count")) and worsened in #str(data.at("summary").at("error_worsened_count")). Median geodesic error changed from #str(rounded(data.at("summary").at("median_source_error_m"), 1)) m to #str(rounded(data.at("summary").at("median_refined_error_m"), 1)) m. The source run had #str(data.at("source_exact_hypothesis_count")) exact-scored hypotheses, #str(data.at("source_exact_unresolved_count")) unresolved hypotheses, and #str(rounded(data.at("source_unknown_fraction") * 100, 1)) percent of its candidate population in an unknown state at interruption. Evaluator artifact SHA-256: #data.at("source_artifact_sha256"). Input manifest SHA-256: #data.at("source_manifest_sha256"). Evaluator source commit: #data.at("source_artifact_commit"). Truth was used only for independent evaluation. Authorship J.C. Vaught.
]
