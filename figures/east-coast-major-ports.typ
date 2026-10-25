#import "@preview/cetz:0.5.3" as cetz

#let sites = csv("../data/sites/east-coast-major-ports-top150-2023.csv").slice(1)
#let all_land = json("../results/eastern-seaboard-maps/data/land-east_coast.json")

#set page(width: 460mm, height: 650mm, margin: 8mm, fill: white)
#set text(size: 8pt, fill: rgb("#111111"))

#let bounds = (-82.0, 24.5, -66.0, 45.5)
#let map_width = 325.0
#let map_height = 520.0
#let site_colors = (
  "#0072B2", "#D55E00", "#009E73", "#AA4499", "#E69F00",
  "#332288", "#56B4E9", "#CC79A7", "#117733", "#444444",
  "#0072B2", "#D55E00", "#009E73", "#AA4499", "#E69F00",
  "#332288", "#56B4E9", "#CC79A7", "#117733", "#444444",
  "#0072B2", "#D55E00",
)

#let xy(lon, lat) = {
  let (west, south, east, north) = bounds
  let longitude_scale = 0.819152
  let x0 = west * longitude_scale
  let x1 = east * longitude_scale
  (
    ((lon * longitude_scale - x0) / (x1 - x0)) * map_width,
    ((lat - south) / (north - south)) * map_height,
  )
}

#let draw-land(polygons) = {
  import cetz.draw: *
  for ring in polygons {
    let points = ring.map(point => xy(point.at(0), point.at(1)))
    if points.len() >= 3 {
      line(..points, close: true, fill: rgb("#E5E1D4"), stroke: (paint: rgb("#35424A"), thickness: 0.22pt))
    }
  }
}

#let coast-map = cetz.canvas(length: 1mm, padding: 0, {
  import cetz.draw: *
  rect((0, 0), (map_width, map_height), fill: rgb("#D8EAF1"), stroke: none)
  draw-land(all_land.at("polygons"))

  for lon in (-80, -76, -72, -68) {
    let (x, _) = xy(lon, 24.5)
    line((x, 0), (x, map_height), stroke: (paint: rgb("#83919A"), thickness: 0.18pt, dash: "dashed"))
    content((x, -7), text(size: 7pt)[#str(lon)° W])
  }
  for lat in (25, 30, 35, 40, 45) {
    let (_, y) = xy(-82, lat)
    line((0, y), (map_width, y), stroke: (paint: rgb("#83919A"), thickness: 0.18pt, dash: "dashed"))
    content((-12, y), text(size: 7pt)[#str(lat)° N])
  }

  for (index, row) in sites.enumerate() {
    let (x, y) = xy(float(row.at(4)), float(row.at(3)))
    let (dx, dy) = if row.at(0) == "B8" { (-4.5, 4.5) } else if row.at(0) == "B9" { (4.5, -4.5) } else { (0.0, 0.0) }
    let color = rgb(site_colors.at(index))
    if dx != 0.0 or dy != 0.0 {
      line((x, y), (x + dx, y + dy), stroke: (paint: rgb("#111111"), thickness: 0.35pt))
    }
    rect((x + dx - 1.8, y + dy - 1.8), (x + dx + 1.8, y + dy + 1.8), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.3pt))
    content((x + dx + 3.0, y + dy + 2.1), text(size: 7pt, weight: "bold", fill: rgb("#111111"))[#row.at(0)])
  }
  rect((0, 0), (map_width, map_height), fill: none, stroke: (paint: rgb("#111111"), thickness: 0.5pt))
})

#align(center)[
  #text(size: 19pt, weight: "bold")[Atlantic Coast major port observer sites]
  #linebreak()
  #text(size: 9pt)[One synthetic water position for each of 22 East Coast ports in the 2023 USACE top 150]
]
#v(4mm)
#grid(columns: (325mm, 100mm), gutter: 7mm, coast-map, [
  #text(size: 10pt, weight: "bold")[Port · 2023 rank]
  #v(2mm)
  #for (index, row) in sites.enumerate() {
    rect(width: 3mm, height: 2.5mm, fill: rgb(site_colors.at(index)), stroke: (paint: rgb("#111111"), thickness: 0.2pt))
    h(1.5mm)
    text(size: 7pt)[#row.at(0) · #row.at(1)]
    linebreak()
    text(size: 6pt, fill: rgb("#333333"))[rank #row.at(2) · #row.at(3)° N, #row.at(4)° W]
    v(1.1mm)
  }
])
#v(3mm)
#text(size: 7pt)[Port scope follows the U.S. Army Corps of Engineers 2023 Leading U.S. Ports table: all Atlantic-coast ports ranked in the top 150 by total tonnage. Site coordinates are the nearest water-cell centers to the listed approximate port-area anchors in the project’s 500 m Natural Earth mask. River-port points are harbor or entrance approaches where the coarse mask does not resolve every inland channel. This map is for experiment planning, not navigation.]
