#import "@preview/cetz:0.5.2"

#set page(width: 360mm, height: 240mm, margin: 11mm, fill: white)
#set text(font: "Arial", size: 9pt, fill: rgb("#000000"))

#let garnet = rgb("#73000A")
#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let sim-bg = rgb("#F8ECEC")
#let search-bg = rgb("#EAF2F7")
#let map-bg = rgb("#EAF3ED")
#let neutral-bg = rgb("#F1F3F5")

#let panel(title, body, fill, edge: ink) = block(
  width: 100%,
  fill: fill,
  stroke: (paint: edge, thickness: 0.9pt),
  inset: 4mm,
)[
  #text(weight: "bold", size: 11pt, fill: edge)[#title]
  #v(1.5mm)
  #body
]

#let file(name, role) = [#text(font: "Menlo", size: 8pt, weight: "bold")[#name] — #role]

#align(center)[
  #text(size: 18pt, weight: "bold")[Bearing-only localization software architecture]
  #v(1mm)
  #text(size: 9pt, fill: muted)[Two independent programs exchange a measurement file; a standalone geospatial component supports both.]
]
#v(5mm)

#text(size: 10pt, weight: "bold")[1. External data and ingestion]
#v(2mm)
#grid(
  columns: (1fr, 1fr, 1fr),
  gutter: 5mm,
  panel([Scenario + sensor config], [Synthetic object inventory, AIS-present flags, boat truth pose, range, noise, and sensor resolution.], neutral-bg),
  panel([AIS feed], [Historical or live AIS records. Normalize timestamps, coordinates, vessel type, dimensions, and AtoN reports when present.], search-bg),
  panel([Shoreline + hydrography], [Read chart / coastline geometry, project to metric coordinates, validate coverage, and rasterize land and water.], map-bg),
)
#v(2mm)
#align(center)[#text(size: 8pt, fill: muted)[Scenario ingest feeds simulation. AIS ingest feeds search. Map ingest builds the neutral, versioned geospatial input.]]
#v(5mm)

#text(size: 10pt, weight: "bold")[2. Separate runtime programs]
#v(2mm)
#grid(
  columns: (1.02fr, 1.08fr, 0.9fr),
  gutter: 5mm,
  panel([SIMULATOR — generates observations], [
    #file([scene_builder.py], [place boat and all scene objects; preserve hidden truth])\
    #file([visibility_filter.py], [apply max range and land-blocked line of sight])\
    #file([angular_detector.py], [merge angularly overlapping objects; report one detection])\
    #file([bearing_model.py], [compute global center bearing; add noise and uncertainty])\
    #file([writers.py], [write measurements, truth, and simulated AIS catalogue separately])
  ], sim-bg, edge: garnet),
  panel([SEARCH — estimates boat position], [
    #file([inputs.py], [read measurements, AIS catalogue, and map version])\
    #file([candidates.py], [generate boat hypotheses only in water])\
    #file([association.py], [match detections to AIS candidates by type and bearing])\
    #file([scoring.py], [score candidate pose with bearing error and visibility])\
    #file([algorithms/], [grid, RANSAC, and refinement methods as swappable solvers])\
    #file([writers.py], [write estimated pose, confidence, and optional associations])
  ], search-bg, edge: rgb("#006BA4")),
  panel([EVALUATOR + BENCHMARK], [
    #file([run_trials.py], [run identical cases through each search method])\
    #file([metrics.py], [position error, success rate, and minimum feature count])\
    #file([timing.py], [stage and total latency; CPU / GPU scaling])\
    #file([report.py], [tables, figures, and reproducible result bundle])
  ], neutral-bg),
)

#v(3mm)
#align(center)[
  #cetz.canvas({
    import cetz.draw: *
    line((0, 0), (3.0, 0), stroke: (paint: garnet, thickness: 1pt), mark: (end: "stealth", fill: garnet))
    content((4.5, 0), [#text(size: 8pt, weight: "bold")[measurements.json]])
    line((6.2, 0), (9.2, 0), stroke: (paint: rgb("#006BA4"), thickness: 1pt), mark: (end: "stealth", fill: rgb("#006BA4")))
    content((10.6, 0), [#text(size: 8pt, weight: "bold")[estimate.json]])
    line((12.2, 0), (15.2, 0), stroke: (paint: ink, thickness: 1pt), mark: (end: "stealth", fill: ink))
    content((16.8, 0), [#text(size: 8pt, weight: "bold")[evaluation]])
  })
]
#v(2mm)
#panel([3. Standalone map / visibility capability], [
  #grid(
    columns: (1fr, 1fr, 1fr),
    gutter: 4mm,
    [#text(weight: "bold")[land_water.py]#linebreak()Versioned projected raster or vector mask. Candidate generation uses water cells; visibility checks use land cells.],
    [#text(weight: "bold")[line_of_sight.py]#linebreak()Independent ray-tracing / segment-query routine. Simulator uses it to hide blocked detections; search uses it to score candidate views.],
    [#text(weight: "bold")[geo_cache.py]#linebreak()Optional tiled / cached lookup for repeated queries. Same map version and resolution are recorded in each experiment.],
  )
], map-bg, edge: muted)
#v(2mm)
#align(center)[#text(size: 8pt, fill: muted)[The visibility component is not the simulator or a search algorithm. Both programs call it through the same stable interface.]]

#pagebreak()
#set page(width: 360mm, height: 240mm, margin: 11mm, fill: white)
#align(center)[
  #text(size: 18pt, weight: "bold")[Proposed files and responsibilities]
  #v(1mm)
  #text(size: 9pt, fill: muted)[A suggested package layout. These are proposed module names, not a claim that the files already exist.]
]
#v(5mm)

#grid(
  columns: (1fr, 1fr),
  gutter: 6mm,
  panel([ingest/ — data adapters], [
    #file([scenario.py], [load scene configuration and generate the synthetic object inventory])\
    #file([ais.py], [read AIS snapshots; normalize vessel and AtoN records])\
    #file([nautical_map.py], [read shoreline / hydrography sources and CRS metadata])\
    #file([schemas.py], [validate inputs and versioned JSON exchange formats])
  ], neutral-bg),
  panel([geo/ — map and visibility], [
    #file([project.py], [transform geographic coordinates into a local metric frame])\
    #file([land_water.py], [build / query the land-water mask])\
    #file([line_of_sight.py], [test whether land blocks a ray between two points])\
    #file([geo_cache.py], [cache tiles and repeated visibility queries])
  ], map-bg, edge: muted),
  panel([simulator/ — measurement generation], [
    #file([cli.py], [run one scene or a batch from configuration])\
    #file([scene_builder.py], [place vessel, markers, and non-AIS objects; keep truth private])\
    #file([visibility_filter.py], [range gate then query land visibility])\
    #file([angular_detector.py], [merge close bearings; select reported class and representative target])\
    #file([bearing_model.py], [center bearing, sensor noise, sigma, and apparent angular width])\
    #file([writers.py], [emit measurements.json, truth.json, and simulated AIS catalogue])
  ], sim-bg, edge: garnet),
  panel([search/ — position estimation], [
    #file([cli.py], [run search against measurement and AIS files])\
    #file([inputs.py], [load reports, AIS records, and map metadata; never read truth])\
    #file([candidates.py], [create coarse water-only position hypotheses])\
    #file([association.py], [resolve unknown object identity and type compatibility])\
    #file([scoring.py], [bearing residual, uncertainty weighting, range, and LOS consistency])\
    #file([algorithms/grid.py], [exhaustive or hierarchical spatial search])\
    #file([algorithms/ransac.py], [sample feature correspondences and propose poses])\
    #file([algorithms/refine.py], [locally refine top candidate poses])\
    #file([writers.py], [emit estimate.json and solver diagnostics])
  ], search-bg, edge: rgb("#006BA4")),
  panel([evaluation/ — scoring and benchmarks], [
    #file([run_trials.py], [pair each measurement set with each search method])\
    #file([metrics.py], [position error, confidence calibration, feature-count threshold])\
    #file([timing.py], [per-stage runtime, full latency, and parallelism measurements])\
    #file([report.py], [save CSV / JSON summaries and publication figures])
  ], neutral-bg),
  panel([schemas/ — program boundary], [
    #file([observation.schema.json], [type label, global bearing, optional bearing sigma / apparent width])\
    #file([truth.schema.json], [true boat position and hidden scene identity; evaluator only])\
    #file([ais.schema.json], [normalized AIS object records and raw source fields])\
    #file([estimate.schema.json], [estimated pose, confidence, optional associations])
  ], neutral-bg),
)
#v(5mm)
#panel([Data separation rule], [
  The simulator may know every object identity, position, size, and AIS-present flag. The search input contains only reported types and bearings, the AIS catalogue, and the map. The evaluator alone joins the estimated position to simulator truth. The shared geo component supplies land / water and line-of-sight queries to both programs without coupling the simulator to the search implementation.
], white)
