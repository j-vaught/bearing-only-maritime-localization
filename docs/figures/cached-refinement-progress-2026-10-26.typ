#import "@preview/lilaq:0.6.0" as lq

#set page(width: 285mm, height: 230mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-refinement-progress-2026-10-26.json")
#let modes = data.at("mode_comparisons")
#let top = data.at("top_refined_mode")
#let ranks = modes.map(row => row.at("source_score_rank"))
#let source-errors = modes.map(row => row.at("source_error_m"))
#let refined-errors = modes.map(row => row.at("refined_error_m"))

#align(center)[
  #text(size: 14pt, weight: "bold")[Cached-candidate refinement diagnostic]
  #linebreak()
  #text(size: 9pt)[Coarse and refined results for 64 evaluator-selected modes. Dated 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8pt, weight: "bold")[Development diagnostic from a partial candidate set. This figure does not establish full-domain localization success or release-gate performance.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[Top refined result]
#text(size: 7.5pt)[The top-ranked refined mode is identified by its refined score rank. Error bars compare that same mode before and after refinement; a nearest alternative is not substituted for the selected estimate.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Top selected mode. Pre-refinement and refined location error],
    xlabel: [Geodesic location error (m)],
    xlim: (0, data.at("plot_error_max_m")),
    ylim: (0.4, 2.6),
    yaxis: (ticks: ((1, [Source candidate]), (2, [Refined result])), subticks: none),
    width: 250mm,
    height: 56mm,
    lq.hbar((top.at("source_error_m"),), (1,)),
    lq.hbar((top.at("refined_error_m"),), (2,)),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (0.8fr, 1fr, 1fr, 1.2fr, 1.2fr, 1.2fr),
    inset: 3pt,
    stroke: 0.5pt + black,
    [Mode key], [Source rank], [Refined rank], [Source error (m)], [Refined error (m)], [Association outcome],
    [#top.at("mode_key")],
    [#str(top.at("source_score_rank"))],
    [#str(top.at("refined_score_rank_within_selected"))],
    [#str(top.at("source_error_m"))],
    [#str(top.at("refined_error_m"))],
    [#top.at("refined_association_label")],
  )
]

#v(3mm)
#text(size: 10pt, weight: "bold")[Score-ranked mode alternatives]
#text(size: 7.5pt)[Each point is one of the 64 source-score-selected modes. Horizontal position follows source score rank within this subset; vertical position is geodesic error. Paired points show the same mode before and after refinement.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Error by source-score rank in the selected partial sample],
    xlabel: [Source score rank among 64 selected modes],
    ylabel: [Geodesic error (m)],
    xlim: (0, 65),
    ylim: (0, data.at("plot_error_max_m")),
    xaxis: (ticks: ((1, [1]), (16, [16]), (32, [32]), (48, [48]), (64, [64])), subticks: none),
    width: 250mm,
    height: 75mm,
    lq.scatter(ranks, source-errors, mark: "s"),
    lq.scatter(ranks, refined-errors, mark: "s"),
  )
]

#v(2mm)
#text(size: 7pt)[
  The source input contains #str(data.at("source_exact_hypothesis_count")) exact-scored hypotheses, of which #str(data.at("source_unknown_fraction") * 100) percent have unknown association. All 64 modes were selected from this partial set. The figure reports evaluator-derived geodesic error and association correctness; truth is used only for evaluation. Source artifact SHA-256: #data.at("source_artifact_sha256"). Source commit: #data.at("source_commit"). Authorship J.C. Vaught.
]
