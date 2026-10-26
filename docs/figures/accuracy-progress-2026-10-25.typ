#import "@preview/lilaq:0.6.0" as lq

#set page(width: 297mm, height: 210mm, margin: 9mm, fill: white)
#set text(size: 8pt)

#let snapshot = json("accuracy-progress-2026-10-25.json")
#let prep = snapshot.at("development_preparation")
#let support = snapshot.at("nominal_support")
#let diagnostic = snapshot.at("a14_candidate_preparation_diagnostic")
#let pending = prep.at("rows_planned") - prep.at("rows_complete")
#let candidate-ids = (1, 2, 3)

#align(center)[
  #text(size: 14pt, weight: "bold")[Accuracy pipeline progress]
  #linebreak()
  #text(size: 9pt)[Interim snapshot dated 2026-10-25. Refresh time was not recorded; figure compiled 2026-10-25 23:50 EDT.]
]

#v(3mm)

#grid(
  columns: (1fr, 1fr),
  column-gutter: 8mm,
  lq.diagram(
    title: [Development preparation. 2,600 of 6,630 rows, 6 of 24 ports],
    xlabel: [Rows in initial development plan],
    xlim: (0, 7000),
    ylim: (0.35, 1.65),
    xaxis: (ticks: ((0, [0]), (2000, [2k]), (4000, [4k]), (6000, [6k])), subticks: none),
    yaxis: (ticks: ((1, [Preparation]),), subticks: none),
    width: 130mm,
    height: 60mm,
    lq.hbar(
      (prep.at("rows_complete"),),
      (1,),
      label: [Completed],
    ),
    lq.hbar(
      (pending,),
      (1,),
      base: prep.at("rows_complete"),
      label: [Remaining],
    ),
  ),
  lq.diagram(
    title: [Nominal support. 101, 196, 203 per label mode],
    xlabel: [Supported placement-realizations per label mode],
    xlim: (0, 220),
    ylim: (0.4, 10.6),
    xaxis: (ticks: ((0, [0]), (50, [50]), (100, [100]), (150, [150]), (200, [200])), subticks: none),
    yaxis: (
      ticks: ((1, [1 km]), (5, [5 km]), (10, [10 km])),
      subticks: none,
    ),
    width: 130mm,
    height: 60mm,
    lq.hbar(
      support.at("placement_realizations_per_label_mode"),
      support.at("range_km"),
    ),
  ),
)

#v(2mm)

#lq.diagram(
  title: [A14 CPU diagnostic. Three fixed-candidate total call times],
  xlabel: [Unprofiled evaluation time (ms)],
  xlim: (450, 510),
  ylim: (0.45, 3.55),
  xaxis: (ticks: ((450, [450]), (470, [470]), (490, [490]), (510, [510])), subticks: none),
  yaxis: (
    ticks: ((1, [Candidate 1]), (2, [Candidate 2]), (3, [Candidate 3])),
    subticks: none,
  ),
  width: 270mm,
  height: 58mm,
  lq.scatter(
    diagnostic.at("candidate_elapsed_ms"),
    candidate-ids,
    label: [Fixed-candidate evaluations],
    mark: "o",
  ),
  lq.plot(
    (diagnostic.at("candidate_elapsed_ms_median"),) * 2,
    (0.5, 3.5),
    label: [Three-call median 488.0 ms],
    mark: none,
    stroke: (dash: "dashed"),
  ),
  lq.plot(
    (diagnostic.at("isolated_static_preparation_median_ms"),) * 2,
    (0.5, 3.5),
    label: [Isolated setup loop median 478.8 ms],
    mark: none,
    stroke: (dash: "dotted"),
  ),
)

#v(3mm)
#text(size: 7pt)[
  66,668 catalog records comprise 2,268 AIS rows and 64,400 chart rows. Of the chart rows, 19,820 are linkage-resolved and 44,580 are identity-limited with geometry retained. A14 profiled three fixed candidates against a four-report development measurement set and a known-coverage map. The 478.8 ms setup figure is an isolated-loop median, about 98% of the 488.0 ms candidate-call median; this directional comparison is not a precise stage timing.
]

#v(2mm)
#text(size: 7pt, weight: "bold")[
  Preparation and support counts are not estimator outcomes. Accuracy, held-out performance, and current GPU speedup are not measured here; field validation with measured bearings and independent truth remains pending.
]
