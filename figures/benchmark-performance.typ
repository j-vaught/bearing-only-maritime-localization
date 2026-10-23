#import "@preview/lilaq:0.6.0" as lq

#set page(width: 190mm, height: 132mm, margin: 6mm, fill: white)
#set text(size: 9pt)

#let rows = csv("data/benchmark_summary.csv").slice(1)
#let xs = rows.map(row => float(row.at(0)))
#let series(field) = rows.map(row => float(row.at(field)))

#grid(
  columns: (1fr, 1fr),
  column-gutter: 7mm,
  row-gutter: 3mm,
  lq.diagram(
    title: [Median full-search latency],
    xlabel: [Maximum visibility range (km)],
    ylabel: [Latency (ms)],
    width: 82mm,
    height: 91mm,
    lq.plot(xs, series(1), mark: "o", label: [Grid, types]),
    lq.plot(xs, series(4), mark: "o", label: [Refined grid, types]),
    lq.plot(xs, series(7), mark: "o", label: [Grid, no types]),
    lq.plot(xs, series(10), mark: "o", label: [Refined grid, no types]),
    lq.plot((5, 10), (100, 100), label: [100 ms frame budget], mark: none, stroke: (dash: "dashed")),
  ),
  lq.diagram(
    title: [Successful fixes across ten trials],
    xlabel: [Maximum visibility range (km)],
    ylabel: [Success rate],
    ylim: (0, 1),
    width: 82mm,
    height: 91mm,
    lq.plot(xs, series(2), mark: "o", label: [Grid, types]),
    lq.plot(xs, series(5), mark: "o", label: [Refined grid, types]),
    lq.plot(xs, series(8), mark: "o", label: [Grid, no types]),
    lq.plot(xs, series(11), mark: "o", label: [Refined grid, no types]),
  ),
)

#align(center)[
  #text(size: 8pt)[
    Synthetic random scenes, 10 trials per setting. The 1 km runs had too few detections to estimate a position and are omitted from the latency plot. The 100 ms line is the entire 10 FPS budget.
  ]
]
