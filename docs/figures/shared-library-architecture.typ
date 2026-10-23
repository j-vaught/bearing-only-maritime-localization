#import "@preview/cetz:0.5.2"

#set page(width: 1300mm, height: 650mm, margin: 18mm, fill: white)
#set text(font: "Arial", size: 10pt, fill: rgb("#000000"))

#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let green = rgb("#EAF3ED")
#let label-size = 10pt
#let node-height = 1.3
#let node-width(label) = label.len() * 0.22 + 0.9

#let box(cx, cy, label) = {
  let width = node-width(label)
  cetz.draw.rect((cx - width / 2, cy - node-height / 2), (cx + width / 2, cy + node-height / 2), fill: green, stroke: (paint: muted, thickness: 1.2pt))
  cetz.draw.content((cx, cy), [#align(center)[#text(size: label-size, weight: "bold", fill: muted)[#label]]])
}

#let connect(parent-x, parent-y, parent-width, children, bus-top, bus-bottom) = {
  let bus-x = parent-x + parent-width / 2 + 1.1
  cetz.draw.line((parent-x + parent-width / 2, parent-y), (bus-x, parent-y), stroke: (paint: ink, thickness: 1.1pt))
  cetz.draw.line((bus-x, bus-top), (bus-x, bus-bottom), stroke: (paint: ink, thickness: 1.1pt))
  for child in children {
    let child-x = child.at(0)
    let child-y = child.at(1)
    let child-width = child.at(2)
    cetz.draw.line((bus-x, child-y), (child-x - child-width / 2, child-y), stroke: (paint: ink, thickness: 1.1pt), mark: (end: "stealth", fill: ink))
  }
}

#align(center)[
  #text(size: 28pt, weight: "bold")[Shared Python library]
  #v(2mm)
  #text(size: 14pt, fill: muted)[An independent package. Consumer programs import only the APIs they need.]
]
#v(5mm)

#cetz.canvas({
  import cetz.draw: *

  let library-y = 29
  let contracts-y = 39
  let geospatial-y = 19
  box(20, library-y, "shared Python library")
  connect(20, library-y, node-width("shared Python library"), ((64, contracts-y, node-width("contracts/")), (64, geospatial-y, node-width("geospatial/"))), contracts-y, geospatial-y)
  box(64, contracts-y, "contracts/")
  box(64, geospatial-y, "geospatial/")

  let contracts = ((110, 45, node-width("observation.py")), (110, 42, node-width("ais.py")), (110, 39, node-width("truth.py")), (110, 36, node-width("estimate.py")), (110, 33, node-width("validation.py")))
  let geospatial = ((110, 25, node-width("projection.py")), (110, 22, node-width("land_water.py")), (110, 19, node-width("line_of_sight.py")), (110, 16, node-width("spatial_index.py")), (110, 13, node-width("bearing_math.py")))
  connect(64, contracts-y, node-width("contracts/"), contracts, 45, 33)
  connect(64, geospatial-y, node-width("geospatial/"), geospatial, 25, 13)
  for (y, label) in ((45, "observation.py"), (42, "ais.py"), (39, "truth.py"), (36, "estimate.py"), (33, "validation.py"), (25, "projection.py"), (22, "land_water.py"), (19, "line_of_sight.py"), (16, "spatial_index.py"), (13, "bearing_math.py")) {
    box(110, y, label)
  }
})
