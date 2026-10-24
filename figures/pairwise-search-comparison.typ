#import "@preview/lilaq:0.6.0" as lq

#set page(width: 220mm, height: 160mm, margin: 6mm, fill: white)
#set text(size: 8pt)

#let rows = csv("data/pairwise-search-summary.csv").slice(1)
#let valid(method) = rows.filter(row => row.at(2) == method and row.at(6) != "")
#let xs(method) = valid(method).map(row => float(row.at(0)))
#let errors(method) = valid(method).map(row => calc.log(float(row.at(6)), base: 10))

#align(center)[
  #lq.diagram(
    title: [Pairwise hypotheses and hierarchical search at B1–B5],
    xlabel: [Observer site index (1 = B1, 2 = B2, …, 5 = B5)],
    ylabel: [Median position error (m), logarithmic scale],
    xlim: (0.5, 5.5),
    ylim: (0, 5),
    width: 194mm,
    height: 105mm,
    lq.plot(
      xs("hierarchical_1km"),
      errors("hierarchical_1km"),
      mark: "o",
      label: [Hierarchical 1 km],
    ),
    lq.plot(
      xs("pairwise_hypothesis"),
      errors("pairwise_hypothesis"),
      mark: "o",
      label: [Pairwise hypotheses],
    ),
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
    Points are medians from three matched synthetic bearing trials per site. Pairwise search returned no estimate at B3 because only one bearing was detected. It localized B4 within 16–50 m, but B4 had only one AIS match and did not meet the three-match benchmark. Both methods met the complete benchmark in 9 of 15 trials. Inputs include the NOAA AIS snapshot, typed ENC aids, and a 500 m water/land mask; bearings are simulated, not measured.
  ]
]
