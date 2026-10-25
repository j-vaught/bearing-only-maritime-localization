#import "@preview/cetz:0.5.3" as cetz

#let prepared = json("../results/eastern-seaboard-maps/data/port-zooms.json")
#let sites = prepared.at("sites")
#let map_size = 160.0
#let colors = ("#0072B2", "#D55E00", "#009E73", "#666666", "#E69F00", "#56B4E9", "#CC79A7", "#444444")

#set page(width: 297mm, height: 210mm, margin: 9mm, fill: white)
#set text(size: 8pt, fill: rgb("#111111"))

#let project(site, lon, lat) = {
  let lon0 = site.at("longitude")
  let lat0 = site.at("latitude")
  let x_km = (lon - lon0) * 111.32 * calc.cos(lat0 * calc.pi / 180)
  let y_km = (lat - lat0) * 111.32
  ((x_km + 15) / 30 * map_size, (y_km + 15) / 30 * map_size)
}

#let ais-color(group) = {
  if group == "Cargo" or group == "Tanker" or group == "Passenger" { colors.at(0) }
  else if group == "Towing" or group == "Service / special craft" or group == "Special operations" or group == "High-speed craft" or group == "Fishing" { colors.at(1) }
  else if group == "Pleasure craft" or group == "Sailing" { colors.at(2) }
  else { colors.at(3) }
}

#let aid-color(group) = {
  if group == "Beacon" { colors.at(4) }
  else if group == "Buoy" { colors.at(5) }
  else if group == "Daymark" { colors.at(6) }
  else if group == "Light" { "#332288" }
  else { colors.at(7) }
}

#let draw-map(site) = {
  import cetz.draw: *
  cetz.canvas(length: 1mm, padding: 0, {
    import cetz.draw: *
    rect((0, 0), (map_size, map_size), fill: rgb("#D8EAF1"), stroke: none)
    for ring in site.at("land") {
      let points = ring.map(point => project(site, point.at(0), point.at(1)))
      if points.len() >= 3 {
        line(..points, close: true, fill: rgb("#E5E1D4"), stroke: (paint: rgb("#222222"), thickness: 0.32pt))
      }
    }
    for tick in (-10, -5, 0, 5, 10) {
      let x = (tick + 15) / 30 * map_size
      let y = (tick + 15) / 30 * map_size
      line((x, 0), (x, map_size), stroke: (paint: rgb("#81909A"), thickness: 0.22pt, dash: "dashed"))
      line((0, y), (map_size, y), stroke: (paint: rgb("#81909A"), thickness: 0.22pt, dash: "dashed"))
      content((x, -4.3), text(size: 6pt)[#str(tick) km])
      content((-7.5, y), text(size: 6pt)[#str(tick) km])
    }
    for row in site.at("aids") {
      let (x, y) = project(site, row.at(1), row.at(2))
      rect((x - 0.42, y - 0.42), (x + 0.42, y + 0.42), fill: rgb(aid-color(row.at(0))), stroke: (paint: rgb("#111111"), thickness: 0.14pt))
    }
    for row in site.at("ais") {
      let (x, y) = project(site, row.at(1), row.at(2))
      rect((x - 0.62, y - 0.62), (x + 0.62, y + 0.62), fill: rgb(ais-color(row.at(0))), stroke: (paint: rgb("#111111"), thickness: 0.2pt))
    }
    let (boat_x, boat_y) = project(site, site.at("longitude"), site.at("latitude"))
    rect((boat_x - 1.2, boat_y - 1.2), (boat_x + 1.2, boat_y + 1.2), fill: rgb("#73000A"), stroke: (paint: rgb("#FFFFFF"), thickness: 0.55pt))
    rect((0, 0), (map_size, map_size), fill: none, stroke: (paint: rgb("#111111"), thickness: 0.55pt))
  })
}

#let legend-item(color, label) = {
  rect(width: 3.2mm, height: 3.2mm, fill: rgb(color), stroke: (paint: rgb("#111111"), thickness: 0.25pt))
  h(2mm)
  text(size: 8pt)[#label]
  linebreak()
  v(1.5mm)
}

#for (index, site) in sites.enumerate() {
  if index > 0 { pagebreak() }
  grid(columns: (170mm, 1fr), gutter: 8mm,
    [
      #text(size: 18pt, weight: "bold")[#site.at("id") · #site.at("port")]
      #linebreak()
      #text(size: 9pt)[2024 USACE rank #site.at("rank") · #site.at("latitude")° N, #calc.abs(site.at("longitude"))° W · 30 km × 30 km view]
      #v(3mm)
      #align(center)[#draw-map(site)]
      #v(2mm)
      #text(size: 7pt, fill: rgb("#333333"))[AIS squares show vessel category. Smaller squares show chart aids; color indicates aid type. Grid is centered on observer at 5 km intervals.]
    ],
    [
      #text(size: 11pt, weight: "bold")[Map key]
      #v(3mm)
      #legend-item("#73000A", "Observer")
      #legend-item(colors.at(0), "Commercial AIS")
      #legend-item(colors.at(1), "Working / service AIS")
      #legend-item(colors.at(2), "Pleasure / sailing AIS")
      #legend-item(colors.at(3), "Other / unknown AIS")
      #legend-item(colors.at(4), "Chart beacon")
      #legend-item(colors.at(5), "Chart buoy")
      #legend-item(colors.at(6), "Chart daymark")
      #legend-item("#332288", "Chart light")
      #legend-item(colors.at(7), "Other chart aid")
      #v(4mm)
      #text(size: 10pt, weight: "bold")[Features in view]
      #v(1.5mm)
      AIS vessels: #site.at("ais").len() \\
      Chart aids: #site.at("aids").len()
      #v(4mm)
      #text(size: 7pt, fill: rgb("#333333"))[AIS snapshot: 15 July 2023, 12:00 UTC. NOAA chart-aid positions are from the bundled ENC aid catalog. Shoreline uses Natural Earth 1:10m land vectors.]
      #v(2mm)
      #text(size: 7pt, fill: rgb("#333333"))[The observer marker is the selected water-cell center for this port. Chart context is for research visualization, not navigation.]
    ],
  )
}
