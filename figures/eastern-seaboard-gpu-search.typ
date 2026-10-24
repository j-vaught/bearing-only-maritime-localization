#import "@preview/lilaq:0.6.0" as lq

#set page(width: 300mm, height: 190mm, margin: 8mm, fill: white)
#set text(size: 9pt)

#let rows = csv("../results/eastern-seaboard-gpu-search/gpu-search.csv").slice(1)
#let selected_rows = rows.filter(row =>
  row.at(0) == "exhaustive_grid" and row.at(7) == "True" and float(row.at(2)) > 1000
)
#let candidates = selected_rows.map(row => calc.log(float(row.at(2)), base: 10))
#let cpu_ms = selected_rows.map(row => calc.log(float(row.at(11)) * 1000, base: 10))
#let gpu_ms = selected_rows.map(row => calc.log(float(row.at(13)) * 1000, base: 10))
#let gpu_total_ms = selected_rows.map(row => calc.log(float(row.at(14)) * 1000, base: 10))
#let spacing = selected_rows.map(row => calc.log(float(row.at(1)), base: 10))
#let approximate_error = selected_rows.map(row => calc.log(float(row.at(19)) / 1000, base: 10))
#let exact_rerank_error = selected_rows.map(row => calc.log(float(row.at(21)) / 1000, base: 10))

#align(center)[
  #text(size: 17pt, weight: "bold")[Full-coast GPU search benchmark]
  #v(2mm)
  #grid(
    columns: (1fr, 1fr),
    column-gutter: 10mm,
    lq.diagram(
      title: [Candidate scoring time],
      xlabel: [log10 candidate positions],
      ylabel: [log10 elapsed milliseconds],
      width: 132mm,
      height: 112mm,
      lq.plot(candidates, cpu_ms, mark: "o", label: [CPU surrogate score]),
      lq.plot(candidates, gpu_ms, mark: "o", label: [GPU score and transfer]),
      lq.plot(candidates, gpu_total_ms, mark: "s", label: [GPU range query plus scoring]),
      lq.plot((3, 6), (2, 2), mark: none, label: [100 ms update budget], stroke: (dash: "dashed")),
    ),
    lq.diagram(
      title: [Localization error on B1],
      xlabel: [log10 grid spacing in km],
      ylabel: [log10 position error in km],
      width: 132mm,
      height: 112mm,
      lq.plot(spacing, approximate_error, mark: "o", label: [GPU surrogate winner]),
      lq.plot(spacing, exact_rerank_error, mark: "s", label: [Best exact match in top 100]),
    ),
  )
  #v(3mm)
  #text(size: 8pt)[
    B1 reports scored against 53,684 East Coast AIS and chart-aid locations with a 10 km range gate. GPU scores independently select the nearest compatible object per report; the exact rerank applies one-to-one association to the top 100 candidates. Search bounds cover 24.5°–45.5° N and 82°–66° W. Land filtering and land-blocked visibility are not included. Timings exclude exact reranking and candidate-grid construction.
  ]
]
