#import "@preview/lilaq:0.6.0" as lq

#set page(width: 260mm, height: 180mm, margin: 8mm, fill: white)
#set text(size: 8pt)

#let data = json("a14-cache-diagnostic-2026-10-26.json")
#let baseline = data.at("baseline")
#let prepared = data.at("prepared")
#let outputs = data.at("candidate_outputs")

#align(center)[
  #text(size: 13pt, weight: "bold")[Candidate-independent preparation diagnostic]
  #linebreak()
  #text(size: 9pt)[Fixed-candidate CPU profile dated 2026-10-26]
]

#v(3mm)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 8mm,
  lq.diagram(
    title: [Per-candidate median. 455.20 to 4.43 ms],
    xlabel: [Median evaluation time (ms)],
    xlim: (0, 500),
    ylim: (0.4, 2.6),
    xaxis: (ticks: ((0, [0]), (100, [100]), (200, [200]), (300, [300]), (400, [400]), (500, [500])), subticks: none),
    yaxis: (ticks: ((1, [Before]), (2, [Prepared])), subticks: none),
    width: 116mm,
    height: 62mm,
    lq.hbar((baseline.at("candidate_median_ms"),), (1,), label: [Before]),
    lq.hbar((prepared.at("candidate_median_ms"),), (2,), label: [Prepared context]),
  ),
  lq.diagram(
    title: [Scorer setup median. 131.31 to 824.98 ms],
    xlabel: [Median setup time (ms)],
    xlim: (0, 900),
    ylim: (0.4, 2.6),
    xaxis: (ticks: ((0, [0]), (200, [200]), (400, [400]), (600, [600]), (800, [800])), subticks: none),
    yaxis: (ticks: ((1, [Before]), (2, [Prepared])), subticks: none),
    width: 116mm,
    height: 62mm,
    lq.hbar((baseline.at("setup_median_ms"),), (1,), label: [Before]),
    lq.hbar((prepared.at("setup_median_ms"),), (2,), label: [Prepared context]),
  ),
)

#v(2mm)
#align(center)[
  #table(
    columns: (1fr, 1fr, 1fr, 1fr),
    inset: 3pt,
    stroke: 0.5pt + black,
    [Fixed candidate], [Before score], [Prepared score], [Predicted groups],
    ..outputs.map(row => (
      [#str(row.at("candidate"))],
      [#str(row.at("score"))],
      [#str(row.at("score"))],
      [#str(row.at("groups"))],
    )).flatten(),
  )
]

#v(2mm)
#text(size: 7pt)[
  The same three fixed projected positions, 66,668-record catalog, and four-report development measurement set were evaluated in both runs. Candidate outputs matched exactly, including scores and predicted group counts. The plotted values are medians across four scorer setups and twelve candidate calls in each diagnostic run. Preparation moved repeated catalog work into scorer initialization, increasing setup time while reducing candidate-call time. These observations are diagnostic rather than controlled latency measurements and do not measure accuracy, held-out performance, calibration, or GPU scaling.
]
