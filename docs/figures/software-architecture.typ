#import "@preview/cetz:0.5.2"

#set page(width: 360mm, height: 240mm, margin: 10mm, fill: white)
#set text(font: "Arial", size: 9pt, fill: rgb("#000000"))

#let garnet = rgb("#73000A")
#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let sim-bg = rgb("#F8ECEC")
#let search-bg = rgb("#EAF2F7")
#let shared-bg = rgb("#EAF3ED")
#let neutral-bg = rgb("#F1F3F5")

#let panel(title, body, fill, edge: ink) = block(
  width: 100%, fill: fill, stroke: (paint: edge, thickness: 0.9pt), inset: 3.5mm,
)[
  #text(weight: "bold", size: 10pt, fill: edge)[#title]
  #v(1.3mm)
  #body
]

#let item(path, role) = [#text(font: "Menlo", size: 7.5pt, weight: "bold")[#path] — #role]

#align(center)[
  #text(size: 18pt, weight: "bold")[A rooted software package tree]
  #v(1mm)
  #text(size: 9pt, fill: muted)[One top-level entry point coordinates independent branches; sibling programs exchange files, not each other's internal code.]
]
#v(4mm)

#panel([main.py — package trunk / orchestrator], [
  Parses the command, loads configuration, selects a run mode, starts the appropriate branch, and records run metadata. It passes versioned files and shared-service handles between branches; it does not contain sensor simulation or localization algorithms.
], neutral-bg)
#v(1mm)
#align(center)[
  #cetz.canvas({
    import cetz.draw: *
    let xs = (1.8, 6.1, 10.4, 14.7, 19.0, 23.3)
    line((12.55, 3.0), (12.55, 2.3), stroke: (paint: ink, thickness: 1pt))
    line((xs.at(0), 2.3), (xs.at(5), 2.3), stroke: (paint: ink, thickness: 1pt))
    for x in xs {
      line((x, 2.3), (x, 1.5), stroke: (paint: ink, thickness: 0.9pt), mark: (end: "stealth", fill: ink))
    }
  })
]
#v(0mm)
#grid(
  columns: (1fr, 1fr, 1fr, 1fr, 1fr, 1fr),
  gutter: 3mm,
  panel([ingest/], [
    #item([scenario.py], [synthetic scene + sensor config])\
    #item([ais.py], [real / simulated AIS catalogue])\
    #item([map.py], [shoreline / hydrography input])
  ], neutral-bg),
  panel([shared/], [
    #item([schemas/], [file contracts])\
    #item([geo/], [projection, land-water, LOS])\
    Public API only; no feature-branch imports.
  ], shared-bg, edge: muted),
  panel([simulator/], [
    #item([main.py], [standalone simulate command])\
    #item([scene.py], [place objects + hidden truth])\
    #item([detect.py], [range, LOS, angular merge])\
    #item([bearing.py], [center + uncertainty])
  ], sim-bg, edge: garnet),
  panel([search/], [
    #item([main.py], [standalone search command])\
    #item([candidates.py], [water-only hypotheses])\
    #item([associate.py], [unknown object identity])\
    #item([algorithms/], [grid, RANSAC, refine])
  ], search-bg, edge: rgb("#006BA4")),
  panel([evaluation/], [
    #item([run.py], [trial orchestration])\
    #item([metrics.py], [error + feature count])\
    #item([timing.py], [stage + total latency])\
    #item([report.py], [tables + figures])
  ], neutral-bg),
  panel([artifacts/], [
    #item([inputs/], [scenario, AIS, map refs])\
    #item([measurements/], [sensor reports])\
    #item([truth/], [evaluator-only truth])\
    #item([results/], [estimates + metrics])
  ], neutral-bg),
)
#v(3mm)
#panel([Branch boundaries], [
  The simulator branch never imports search internals, and search never imports simulator internals. Both may call the stable shared/geo API; they do not call one another. The simulator writes observations and hidden truth to separate artifacts. Main passes observations and AIS to search, then passes estimate plus truth to evaluation.
], white)

#pagebreak()
#set page(width: 360mm, height: 240mm, margin: 10mm, fill: white)
#align(center)[
  #text(size: 18pt, weight: "bold")[Expanded branch tree and file roles]
  #v(1mm)
  #text(size: 9pt, fill: muted)[Proposed directory and file names. Each branch has its own entry point and owns its implementation.]
]
#v(4mm)

#grid(
  columns: (1fr, 1fr), gutter: 5mm,
  panel([main.py → ingest/], [
    #item([ingest/main.py], [entry point for ingestion commands])\
    #item([ingest/scenario.py], [load configuration; generate object inventory with AIS-present flags])\
    #item([ingest/ais.py], [read records; normalize time, position, type, dimensions, and AtoN type])\
    #item([ingest/map.py], [load coastline / hydrography, CRS, and source metadata])\
    Main chooses the ingest operation and writes normalized inputs for the later branch.
  ], neutral-bg),
  panel([main.py → shared/], [
    #item([shared/schemas/observation.py], [type label, global bearing, optional bearing sigma and apparent width])\
    #item([shared/schemas/truth.py], [true boat pose and hidden scene IDs; evaluator only])\
    #item([shared/schemas/estimate.py], [estimated pose, confidence, optional associations])\
    #item([shared/geo/project.py], [convert lat/lon to local metric coordinates])\
    #item([shared/geo/land_water.py], [build and query versioned land-water mask])\
    #item([shared/geo/line_of_sight.py], [ray / segment test; same API for both programs])
  ], shared-bg, edge: muted),
  panel([main.py → simulator/], [
    #item([simulator/main.py], [standalone simulation command])\
    #item([simulator/scene.py], [place boat and objects; preserve hidden truth])\
    #item([simulator/visibility.py], [apply range gate, then shared land-occlusion query])\
    #item([simulator/detection.py], [merge angularly close detections; select reported type])\
    #item([simulator/bearing.py], [center of object's angular extent; add noise / uncertainty])\
    #item([simulator/write.py], [write measurements, truth, and scenario AIS catalogue separately])
  ], sim-bg, edge: garnet),
  panel([main.py → search/], [
    #item([search/main.py], [standalone localization command])\
    #item([search/inputs.py], [read measurements + AIS; reject truth input])\
    #item([search/candidates.py], [generate coarse candidates on water])\
    #item([search/association.py], [compare reported type / bearing to unknown AIS identity])\
    #item([search/scoring.py], [bearing residual, uncertainty weight, LOS consistency])\
    #item([search/algorithms/grid.py], [exhaustive or hierarchical grid])\
    #item([search/algorithms/ransac.py], [sample correspondence hypotheses])\
    #item([search/algorithms/refine.py], [refine best candidate positions])\
    #item([search/write.py], [write estimate + diagnostics])
  ], search-bg, edge: rgb("#006BA4")),
  panel([main.py → evaluation/], [
    #item([evaluation/main.py], [standalone evaluation / benchmark command])\
    #item([evaluation/run.py], [run fixed cases across search methods])\
    #item([evaluation/metrics.py], [position error, success, minimum features])\
    #item([evaluation/timing.py], [per-stage, end-to-end, CPU / GPU timing])\
    #item([evaluation/report.py], [write CSV / JSON and publication figures])
  ], neutral-bg),
  panel([main.py → artifacts/], [
    #item([inputs/scene.json], [simulator scene configuration])\
    #item([inputs/ais.json], [AIS catalogue passed to search])\
    #item([inputs/map/], [versioned map/mask data])\
    #item([runs/<id>/measurements.json], [search input])\
    #item([runs/<id>/truth.json], [evaluation input only])\
    #item([runs/<id>/estimate.json], [search output])\
    #item([runs/<id>/metrics.json], [evaluation output])
  ], neutral-bg),
)
#v(4mm)
#panel([Execution path], [
  #text(font: "Menlo", size: 8pt, weight: "bold")[main.py → ingest → simulator → measurements.json → search → estimate.json → evaluation]
  #linebreak()
  #text(font: "Menlo", size: 8pt, weight: "bold")[simulator → truth.json → evaluation only]
  #linebreak()
  The map and AIS inputs are passed as data; search does not reach into simulator to get them.
], white)
