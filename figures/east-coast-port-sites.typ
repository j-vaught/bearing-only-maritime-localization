#import "@preview/cetz:0.5.3" as cetz

#let sites = csv("../data/sites/east-coast-ports-b1-b10.csv").slice(1)
#let all_land = json("../results/eastern-seaboard-maps/data/land-east_coast.json")

#set page(width: 460mm, height: 650mm, margin: 8mm, fill: white)
#set text(size: 8pt, fill: rgb("#111111"))

#let bounds = (-82.0, 24.5, -66.0, 45.5)
#let map_width = 325.0
#let map_height = 520.0
#let site_colors = (
  "#0072B2", "#D55E00", "#009E73", "#AA4499", "#E69F00",
  "#332288", "#56B4E9", "#CC79A7", "#117733", "#444444",
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
    let (x, y) = xy(float(row.at(3)), float(row.at(2)))
    let color = rgb(site_colors.at(index))
    rect((x - 2.1, y - 2.1), (x + 2.1, y + 2.1), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.35pt))
    content((x + 3.5, y + 2.5), text(size: 8pt, weight: "bold", fill: rgb("#111111"))[#row.at(0)])
  }
  rect((0, 0), (map_width, map_height), fill: none, stroke: (paint: rgb("#111111"), thickness: 0.5pt))
})

#align(center)[
  #text(size: 19pt, weight: "bold")[East Coast port-area observer sites]
  #linebreak()
  #text(size: 9pt)[Ten synthetic test positions from Boston to Miami · squares show B1–B10]
]
#v(4mm)
#grid(columns: (325mm, 100mm), gutter: 7mm, coast-map, [
  #text(size: 10pt, weight: "bold")[Port area and site]
  #v(2mm)
  #for (index, row) in sites.enumerate() {
    rect(width: 3mm, height: 2.5mm, fill: rgb(site_colors.at(index)), stroke: (paint: rgb("#111111"), thickness: 0.2pt))
    h(1.5mm)
    text(size: 7.5pt)[#row.at(0) · #row.at(1)]
    linebreak()
    text(size: 6.5pt, fill: rgb("#333333"))[#row.at(2)° N, #row.at(3)° W]
    v(2mm)
  }
])
#v(3mm)
#text(size: 7pt)[Coordinates are in data/sites/east-coast-ports-b1-b10.csv. All ten points fall in water cells in the project's 500 m Natural Earth land/water mask. Savannah and Jacksonville are river-port approaches because the coarse mask does not resolve their inland shipping channels. This map is for experiment planning, not navigation.]
