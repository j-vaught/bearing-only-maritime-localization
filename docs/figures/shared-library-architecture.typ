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
  #text(size: 14pt, fill: muted)[Independent package imported by simulator, search, and evaluation through shared contracts and geometry.]
]
#v(5mm)

#cetz.canvas({
  import cetz.draw: *

  let library-y = 29
  let contracts-y = 41
  let geospatial-y = 20
  let serialization-y = 29
  box(20, library-y, "maritime_localization_shared")

  let root-children = (
    (64, contracts-y, node-width("contracts/")),
    (64, geospatial-y, node-width("geospatial/")),
    (110, serialization-y, node-width("serialization.py")),
  )
  connect(20, library-y, node-width("maritime_localization_shared"), root-children, contracts-y, geospatial-y)
  box(64, contracts-y, "contracts/")
  box(64, geospatial-y, "geospatial/")
  box(110, serialization-y, "serialization.py")

  let contracts = (
    (110, 49, node-width("observation.py")),
    (110, 46, node-width("ais.py")),
    (110, 43, node-width("map_input.py")),
    (110, 40, node-width("estimate.py")),
    (110, 37, node-width("truth.py")),
  )
  let geospatial = (
    (110, 26, node-width("water_mask.py")),
    (110, 23, node-width("raster_los.py")),
    (110, 20, node-width("vector_los.py")),
    (110, 17, node-width("spatial_index.py")),
  )
  connect(64, contracts-y, node-width("contracts/"), contracts, 49, 37)
  connect(64, geospatial-y, node-width("geospatial/"), geospatial, 26, 17)
  for (y, label) in ((49, "observation.py"), (46, "ais.py"), (43, "map_input.py"), (40, "estimate.py"), (37, "truth.py"), (26, "water_mask.py"), (23, "raster_los.py"), (20, "vector_los.py"), (17, "spatial_index.py")) {
    box(110, y, label)
  }
})
