#import "@preview/lilaq:0.6.0" as lq

#set page(width: 220mm, height: 160mm, margin: 6mm, fill: white)
#set text(size: 8pt)

#let rows = csv("data/eastern-seaboard-gpu-scaling.csv").slice(1)
#let xs = rows.map(row => calc.log(float(row.at(1)), base: 10))
#let ys = rows.map(row => calc.log(float(row.at(2)), base: 10))

#align(center)[
  #lq.diagram(
    title: [Coast-wide GPU candidate-scoring cost],
    xlabel: [log10 of candidate positions],
    ylabel: [log10 of scoring time in milliseconds],
    xlim: (4.4, 6.7),
    ylim: (1.8, 4.5),
    width: 194mm,
    height: 105mm,
    lq.plot(
      xs,
      ys,
      mark: "s",
      label: [Measured grid runs],
    ),
    lq.plot(
      (4.4, 6.7),
      (2, 2),
      label: [100 ms update budget],
      mark: none,
      stroke: (dash: "dashed"),
    ),
  )
  #v(2mm)
  #text(size: 7.5pt)[
    RTX 6000 Ada scoring 23 B1 simulated reports against 2,695 AIS and chart-aid points. The candidate grid covers 24.5°–45.5° N and 82°–66° W, includes land, and uses a 10 km range gate. Water filtering, line-of-sight checks, and hypothesis generation are excluded.
  ]
]
