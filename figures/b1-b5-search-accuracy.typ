#import "@preview/lilaq:0.6.0" as lq

#set page(width: 205mm, height: 153mm, margin: 6mm, fill: white)
#set text(size: 8pt)

#let rows = csv("data/b1-b5-accuracy.csv").slice(1)
#let xs = (1, 2, 3, 4, 5)
#let errors(method) = rows.filter(row => row.at(2) == method).map(row => calc.log(float(row.at(3)), base: 10))

#align(center)[
  #lq.diagram(
    title: [B1–B5 localization error at 10 km visibility],
    xlabel: [Observer site, ordered B1 through B5 from left to right],
    ylabel: [log₁₀ position error (m)],
    xlim: (0.5, 5.5),
    ylim: (2, 6.5),
    width: 185mm,
    height: 105mm,
    lq.plot(xs, errors("grid"), mark: "o", label: [Grid]),
    lq.plot(xs, errors("randomized"), mark: "o", label: [Randomized]),
    lq.plot(xs, errors("hierarchical"), mark: "o", label: [Hierarchical]),
    lq.plot(
      (0.5, 5.5),
      (calc.log(250, base: 10), calc.log(250, base: 10)),
      label: [250 m position threshold],
      mark: none,
      stroke: (dash: "dashed"),
    ),
  )
  #v(2mm)
  #text(size: 7.5pt)[
    Points show median error across three seeded noise realizations. AIS bearing counts at B1–B5 were 8, 3, 1, 1, and 9. Success also requires three correct AIS associations. Synthetic bearings use a July 2023 AIS snapshot and charted aids; observations are simulated, not measured. Search area is 114 × 333 km with water and land visibility constraints.
  ]
]
