#import "@preview/lilaq:0.6.0" as lq

#set page(width: 240mm, height: 297mm, margin: 14mm, fill: white)
#set text(size: 10pt)
#let d = json("localization-recovery-summary-2026-10-26.json")
#let before = d.at("before_expansion_score_winner")
#let after = d.at("after_expansion_score_winner")
#let trajectory = d.at("same_mode_local_trajectory")
#let round(value, places) = calc.round(value * calc.pow(10, places)) / calc.pow(10, places)

#align(center)[
  #text(size: 18pt, weight: "bold")[Localization recovery in a selected development subset]
  #linebreak()
  #text(size: 10pt)[Single-addition expansion diagnostic · 2026-10-26]
]
#v(3mm)
#text(size: 12pt, weight: "bold")[The returned score winner changed from 853,439.780 m error to 11.127 m.]
#text(size: 10pt)[Before expansion, the assigned-fit score winner had 0 of 4 correct assignments. After expansion, the lowest-score completed result had 5 of 5 correct assignments and ranks 1 of 64 in the composite selected-mode audit. These are different modes, selected by search score within their stages.]
#v(3mm)
#align(center)[
  #lq.diagram(
    title: [Geodesic error of the actual score winner at each stage],
    xlabel: [Stage score winner],
    ylabel: [Error to evaluator truth (m, logarithmic scale)],
    xlim: (0.5, 2.5),
    ylim: (1, 1000000),
    yscale: "log",
    xaxis: (ticks: ((1, [Before expansion]), (2, [After expansion])), subticks: none),
    yaxis: (ticks: ((10, [10]), (100, [100]), (1000, [1,000]), (10000, [10,000]), (100000, [100,000]), (1000000, [1,000,000])), subticks: none),
    width: 190mm,
    height: 68mm,
    lq.scatter((1, 2), (before.at("error_m"), after.at("error_m")), mark: "s"),
  )
]
#align(center)[#text(size: 9pt)[Before. #str(round(before.at("error_m"), 3)) m · score #str(round(before.at("score"), 6)) · #str(before.at("correct_assignments"))/#str(before.at("assignment_count")) correct.  After. #str(round(after.at("error_m"), 3)) m · score #str(round(after.at("score"), 6)) · #str(after.at("correct_assignments"))/#str(after.at("assignment_count")) correct.]]
#v(3mm)
#text(size: 12pt, weight: "bold")[One mode’s error progression]
#text(size: 10pt)[For the same mode, the location error decreased across cached source, pattern refinement, assigned fitting, and expansion.]
#v(2mm)
#align(center)[
  #lq.diagram(
    title: [Same-mode geodesic error],
    xlabel: [Error to evaluator truth (m)],
    xlim: (0, 325),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (100, [100]), (200, [200]), (300, [300])), subticks: none),
    yaxis: (ticks: ((1, [Cached source]), (2, [Pattern refinement]), (3, [Assigned fit]), (4, [Single-addition expansion])), subticks: none),
    width: 190mm,
    height: 44mm,
    lq.hbar((trajectory.at(0).at("error_m"),), (1,)),
    lq.hbar((trajectory.at(1).at("error_m"),), (2,)),
    lq.hbar((trajectory.at(2).at("error_m"),), (3,)),
    lq.hbar((trajectory.at(3).at("error_m"),), (4,)),
  )
]
#align(center)[#text(size: 9pt)[296.286 m → 266.378 m → 181.279 m → 11.127 m. This is a single selected mode, not a population accuracy estimate.]]
#v(4mm)
#block(stroke: 0.8pt + black, inset: 5pt)[
  #text(size: 10pt, weight: "bold")[Scope and status]
  The composite audit covers 64 of 64 selected modes. The serial run stopped at 60/64 on its deadline; the parallel run stopped at 63/64 after disk interruption at 568.419 s. The composite is not a completed parallel run or a combined latency result. The source search stopped with 1,410,282 hypotheses unknown. This development result is not an overall accuracy rate or an accepted, calibrated fix. Evaluator truth was used only after each estimate to measure error and assignment correctness.
]
#v(3mm)
#text(size: 8pt)[Source paths and exact SHA-256 values are recorded in the adjacent JSON. Composite audit digest begins #d.at("source_artifacts").at(3).at("sha256").slice(0, 12). Authorship J.C. Vaught.]
