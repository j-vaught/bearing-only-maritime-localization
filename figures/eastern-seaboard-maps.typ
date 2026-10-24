#import "@preview/cetz:0.5.3" as cetz

#let ais = csv("../results/eastern-seaboard-maps/data/ais.csv").slice(1)
#let aids = csv("../results/eastern-seaboard-maps/data/chart-aids.csv").slice(1)
#let sites = csv("../results/eastern-seaboard-maps/data/sites.csv").slice(1)
#let all_land = json("../results/eastern-seaboard-maps/data/land-east_coast.json")
#let bay_land = json("../results/eastern-seaboard-maps/data/land-chesapeake.json")
#let map_meta = json("../results/eastern-seaboard-maps/data/manifest.json")

#set page(width: 460mm, height: 650mm, margin: 8mm, fill: white)
#set text(size: 8pt, fill: rgb("#111111"))

#let AIS_GROUPS = (
  "Fishing",
  "Towing",
  "Special operations",
  "Sailing",
  "Pleasure craft",
  "High-speed craft",
  "Service / special craft",
  "Passenger",
  "Cargo",
  "Tanker",
  "Unknown / other",
)
#let AID_GROUPS = (
  "Beacon",
  "Buoy",
  "Daymark",
  "Fog",
  "Light",
  "Radar",
  "Radio",
  "Retro",
  "Topmark",
)
#let AIS_COLORS = (
  "Fishing": rgb("#0072B2"),
  "Towing": rgb("#E69F00"),
  "Special operations": rgb("#009E73"),
  "Sailing": rgb("#F0E442"),
  "Pleasure craft": rgb("#56B4E9"),
  "High-speed craft": rgb("#D55E00"),
  "Service / special craft": rgb("#CC79A7"),
  "Passenger": rgb("#332288"),
  "Cargo": rgb("#88CCEE"),
  "Tanker": rgb("#AA4499"),
  "Unknown / other": rgb("#444444"),
)
#let AID_COLORS = (
  "Beacon": rgb("#D55E00"),
  "Buoy": rgb("#0072B2"),
  "Daymark": rgb("#009E73"),
  "Fog": rgb("#CC79A7"),
  "Light": rgb("#E69F00"),
  "Radar": rgb("#332288"),
  "Radio": rgb("#56B4E9"),
  "Retro": rgb("#AA4499"),
  "Topmark": rgb("#117733"),
)
#let SITE_COLORS = (
  "B1": rgb("#0072B2"),
  "B2": rgb("#D55E00"),
  "B3": rgb("#009E73"),
  "B4": rgb("#AA4499"),
  "B5": rgb("#E69F00"),
)

#let coast_bounds = (-82.0, 24.5, -66.0, 45.5)
#let bay_bounds = (-77.35, 36.2, -75.2, 40.0)
#let coast_width = 325.0
#let coast_height = 520.0
#let bay_width = 85.0
#let bay_height = 150.0

#let xy = (lon, lat, bounds, width, height) => {
  let (west, south, east, north) = bounds
  let longitude_scale = 0.819152
  let x0 = west * longitude_scale
  let x1 = east * longitude_scale
  (
    ((lon * longitude_scale - x0) / (x1 - x0)) * width,
    ((lat - south) / (north - south)) * height,
  )
}

#let draw-land(polygons, bounds, width, height) = {
  import cetz.draw: *
  for ring in polygons {
    let points = ring.map(point => xy(point.at(0), point.at(1), bounds, width, height))
    if points.len() >= 3 {
      line(..points, close: true, fill: rgb("#E5E1D4"), stroke: (paint: rgb("#35424A"), thickness: 0.22pt))
    }
  }
}

#let draw-grid(bounds, width, height) = {
  import cetz.draw: *
  let (west, south, east, north) = bounds
  for lon in (-80, -76, -72, -68) {
    let (x, _) = xy(lon, south, bounds, width, height)
    line((x, 0), (x, height), stroke: (paint: rgb("#83919A"), thickness: 0.18pt, dash: "dashed"))
    content((x, -7), text(size: 7pt)[#str(lon)° W])
  }
  for lat in (25, 30, 35, 40, 45) {
    let (_, y) = xy(west, lat, bounds, width, height)
    line((0, y), (width, y), stroke: (paint: rgb("#83919A"), thickness: 0.18pt, dash: "dashed"))
    content((-12, y), text(size: 7pt)[#str(lat)° N])
  }
}

#let map-canvas(kind) = cetz.canvas(length: 1mm, padding: 0, {
  import cetz.draw: *
  rect((0, 0), (coast_width, coast_height), fill: rgb("#D8EAF1"), stroke: none)
  draw-land(all_land.at("polygons"), coast_bounds, coast_width, coast_height)
  draw-grid(coast_bounds, coast_width, coast_height)

  if kind == "ais" {
    for row in ais {
      let (x, y) = xy(float(row.at(0)), float(row.at(1)), coast_bounds, coast_width, coast_height)
      let color = AIS_COLORS.at(row.at(3))
      rect((x - 0.75, y - 0.75), (x + 0.75, y + 0.75), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.12pt))
    }
  } else if kind == "aids" {
    for row in aids {
      let (x, y) = xy(float(row.at(0)), float(row.at(1)), coast_bounds, coast_width, coast_height)
      let color = AID_COLORS.at(row.at(2))
      rect((x - 0.42, y - 0.42), (x + 0.42, y + 0.42), fill: color, stroke: none)
    }
  } else if kind == "combined" {
    for row in aids {
      let (x, y) = xy(float(row.at(0)), float(row.at(1)), coast_bounds, coast_width, coast_height)
      rect((x - 0.38, y - 0.38), (x + 0.38, y + 0.38), fill: rgb("#444444"), stroke: none)
    }
    for row in ais {
      let (x, y) = xy(float(row.at(0)), float(row.at(1)), coast_bounds, coast_width, coast_height)
      let color = AIS_COLORS.at(row.at(3))
      rect((x - 0.95, y - 0.95), (x + 0.95, y + 0.95), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.1pt))
    }
  } else if kind == "sites" {
    let bay_frame = xy(-77.35, 36.2, coast_bounds, coast_width, coast_height)
    let bay_frame_ne = xy(-75.2, 40.0, coast_bounds, coast_width, coast_height)
    rect(bay_frame, bay_frame_ne, fill: none, stroke: (paint: rgb("#111111"), thickness: 0.7pt))
    for row in sites {
      let (x, y) = xy(float(row.at(1)), float(row.at(2)), coast_bounds, coast_width, coast_height)
      let color = SITE_COLORS.at(row.at(0))
      rect((x - 2.2, y - 2.2), (x + 2.2, y + 2.2), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.35pt))
      content((x + 4.2, y + 3.0), text(size: 9pt, weight: "bold", fill: rgb("#111111"))[#row.at(0)])
    }
  }
})

#let bay-canvas = cetz.canvas(length: 1mm, padding: 0, {
  import cetz.draw: *
  rect((0, 0), (bay_width, bay_height), fill: rgb("#D8EAF1"), stroke: none)
  draw-land(bay_land.at("polygons"), bay_bounds, bay_width, bay_height)
  for row in sites {
    let (x, y) = xy(float(row.at(1)), float(row.at(2)), bay_bounds, bay_width, bay_height)
    let color = SITE_COLORS.at(row.at(0))
    rect((x - 2.0, y - 2.0), (x + 2.0, y + 2.0), fill: color, stroke: (paint: rgb("#111111"), thickness: 0.35pt))
    content((x + 3.2, y + 2.8), text(size: 8pt, weight: "bold", fill: rgb("#111111"))[#row.at(0)])
  }
  rect((0, 0), (bay_width, bay_height), fill: none, stroke: (paint: rgb("#111111"), thickness: 0.5pt))
})

#let color-key(color, label) = [
  #rect(width: 3mm, height: 2.5mm, fill: color, stroke: (paint: rgb("#111111"), thickness: 0.2pt))
  #h(1.5mm)
  #label
]

#let ais-legend = [
  #text(size: 10pt, weight: "bold")[AIS vessel class]
  #v(2mm)
  #for group in AIS_GROUPS {
    let count = ais.filter(row => row.at(3) == group).len()
    color-key(AIS_COLORS.at(group), [#group (#count)])
    v(1.3mm)
  }
]

#let aid-legend = [
  #text(size: 10pt, weight: "bold")[Chart-aid type]
  #v(2mm)
  #for group in AID_GROUPS {
    let count = aids.filter(row => row.at(2) == group).len()
    color-key(AID_COLORS.at(group), [#group (#count)])
    v(1.3mm)
  }
]

#let map-title(title, subtitle) = [
  #align(center)[
    #text(size: 19pt, weight: "bold")[#title]
    #linebreak()
    #text(size: 9pt)[#subtitle]
  ]
  #v(4mm)
]

#let source-footer(note) = [
  #v(3mm)
  #text(size: 7pt)[#note]
]

#map-title("East Coast AIS positions by reported vessel class", "Single one-minute snapshot · 15 July 2023, 12:00–12:01 UTC · 2,268 vessel positions")
#grid(columns: (325mm, 100mm), gutter: 7mm, map-canvas("ais"), ais-legend)
#source-footer("AIS type codes are grouped into broad navigation classes. Open land/water basemap: Natural Earth 1:10m, version 5.1.1. This regional view is for data exploration, not navigation.")

#pagebreak()
#map-title("East Coast charted aids to navigation", "51,399 distinct plotted locations · grouped by NOAA ENC feature family")
#grid(columns: (325mm, 100mm), gutter: 7mm, map-canvas("aids"), aid-legend)
#source-footer("NOAA ENC Direct to GIS snapshot retrieved 24 October 2026. Cross-scale repeats were removed for this map by feature family and coordinates rounded to five decimal places. Basemap: Natural Earth 1:10m, version 5.1.1.")

#pagebreak()
#map-title("AIS positions and charted aids together", "2,268 AIS vessel positions and 51,399 deduplicated chart-aid locations")
#grid(columns: (325mm, 100mm), gutter: 7mm, map-canvas("combined"), [
  #text(size: 10pt, weight: "bold")[Map layers]
  #v(2mm)
  #color-key(rgb("#444444"), [Chart aids])
  #v(1.3mm)
  #for group in AIS_GROUPS {
    let count = ais.filter(row => row.at(3) == group).len()
    color-key(AIS_COLORS.at(group), [AIS · #group (#count)])
    v(1.3mm)
  }
])
#source-footer("AIS is plotted above chart aids so vessel classes remain visible where the layers overlap. NOAA ENC aid points include all six scale bands with cross-scale duplicates removed. Basemap: Natural Earth 1:10m, version 5.1.1.")

#pagebreak()
#map-title("B1–B5 observer locations", "All five water-only test sites shown on the East Coast extent, with a Chesapeake Bay zoom inset")
#grid(columns: (325mm, 100mm), gutter: 7mm, map-canvas("sites"), [
  #text(size: 10pt, weight: "bold")[Chesapeake inset]
  #v(2mm)
  #bay-canvas
  #v(4mm)
  #for row in sites {
    color-key(SITE_COLORS.at(row.at(0)), [#row.at(0) · #row.at(2)° N, #row.at(1)° W])
    v(1.5mm)
  }
])
#source-footer("Site coordinates are from data/sites/b1-b5.csv. The inset shows the land/water coast context and the relative separation of the five test locations. Basemap: Natural Earth 1:10m, version 5.1.1.")
