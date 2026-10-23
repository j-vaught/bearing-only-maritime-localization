#import "@preview/lilaq:0.6.0" as lq

#set page(width: 205mm, height: 153mm, margin: 6mm, fill: white)
#set text(size: 8pt)

#let rows = csv("data/b1-b5-estimates.csv").slice(1)
#let get-row(site, method) = rows.find(row => row.at(0) == site and row.at(1) == method)
#let east(site, method) = float(get-row(site, method).at(2))
#let north(site, method) = float(get-row(site, method).at(3))
#let one(value) = (value,)

#align(center)[
  #text(size: 14pt, weight: "bold")[B1 and B5 estimate offsets from the true location]
  #v(2mm)
  #grid(
    columns: (1fr, 1fr),
    column-gutter: 4mm,
    lq.diagram(
      title: [B1, local view],
      xlabel: [East offset from truth (km)],
      ylabel: [North offset from truth (km)],
      xlim: (-2, 2),
      ylim: (-2, 2),
      width: 91mm,
      height: 84mm,
      lq.plot(one(east("B1", "Actual")), one(north("B1", "Actual")), mark: "o", label: [Actual]),
      lq.plot(one(east("B1", "Grid")), one(north("B1", "Grid")), mark: "o", label: [Grid]),
      lq.plot(one(east("B1", "Hierarchical")), one(north("B1", "Hierarchical")), mark: "o", label: [Hierarchical]),
    ),
    lq.diagram(
      title: [B5, local view],
      xlabel: [East offset from truth (km)],
      ylabel: [North offset from truth (km)],
      xlim: (-2, 2),
      ylim: (-2, 2),
      width: 91mm,
      height: 84mm,
      lq.plot(one(east("B5", "Actual")), one(north("B5", "Actual")), mark: "o", label: [Actual]),
      lq.plot(one(east("B5", "Grid")), one(north("B5", "Grid")), mark: "o", label: [Grid]),
      lq.plot(one(east("B5", "Hierarchical")), one(north("B5", "Hierarchical")), mark: "o", label: [Hierarchical]),
    ),
  )
  #v(2mm)
  #text(size: 8pt)[Each panel is a ±2 km view centered on truth. Markers show estimates from the first paired 10 km trial with type labels. B1 grid and hierarchical errors are 648 m and 121 m; B5 errors are 757 m and 1,153 m. Randomized estimates are outside the plotted windows: B1 is 289.4 km southeast, and B5 is 264.3 km northwest of truth.]
]
