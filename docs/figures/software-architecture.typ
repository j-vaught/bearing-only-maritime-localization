#import "@preview/cetz:0.5.2"

#set page(width: 1600mm, height: 1300mm, margin: 18mm, fill: white)
#set text(font: "Arial", size: 10pt, fill: rgb("#000000"))

#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let garnet = rgb("#73000A")
#let green = rgb("#EAF3ED")
#let rose = rgb("#F8ECEC")
#let blue = rgb("#EAF2F7")
#let gray = rgb("#F1F3F5")
#let blue-edge = rgb("#006BA4")
#let label-size = 10pt
#let node-height = 1.3

#let node-width(label) = label.len() * 0.22 + 0.9

#let box(cx, cy, label, fill: white, edge: ink) = {
  let width = node-width(label)
  cetz.draw.rect((cx - width / 2, cy - node-height / 2), (cx + width / 2, cy + node-height / 2), fill: fill, stroke: (paint: edge, thickness: 1.2pt))
  cetz.draw.content((cx, cy), [#align(center)[#text(size: label-size, weight: "bold", fill: edge)[#label]]])
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
  #text(size: 28pt, weight: "bold")[Software package tree]
  #v(2mm)
  #text(size: 14pt, fill: muted)[Horizontal view from main.py through simulator, search, and evaluation. The shared Python library is shown separately.]
]
#v(5mm)

#cetz.canvas({
  import cetz.draw: *

  // External data providers feed the main entry point from outside the package tree.
  let main-y = 45.4
  box(4, main-y, "External inputs", fill: gray, edge: muted)
  content((4, main-y - 1.6), [#align(center)[#text(size: 8pt, fill: muted)[AIS • charts • scene and sensor settings]]])
  box(19, main-y, "main.py", fill: gray)
  let input-width = node-width("External inputs")
  let main-width = node-width("main.py")
  line((4 + input-width / 2, main-y), (19 - main-width / 2, main-y), stroke: (paint: garnet, thickness: 1.6pt), mark: (end: "stealth", fill: garnet))

  // Each top-level branch occupies a separate vertical band and expands left to right.
  let simulator-y = 77.9
  let search-y = 45.9
  let evaluation-y = 12.3
  connect(19, main-y, main-width, ((48, simulator-y, node-width("simulator/")), (48, search-y, node-width("search/")), (48, evaluation-y, node-width("evaluation/"))), simulator-y, evaluation-y)
  box(48, simulator-y, "simulator/", fill: rose, edge: garnet)
  box(48, search-y, "search/", fill: blue, edge: blue-edge)
  box(48, evaluation-y, "evaluation/", fill: gray)

  // simulator/ owns scene setup, visibility, detection, reports, and configuration.
  let sim-scene-y = 89.5
  let sim-visibility-y = 84
  let sim-detection-y = 77.75
  let sim-reports-y = 71.5
  let sim-config-y = 66.75
  connect(48, simulator-y, node-width("simulator/"), ((82, sim-scene-y, node-width("scene/")), (82, sim-visibility-y, node-width("visibility/")), (82, sim-detection-y, node-width("detection/")), (82, sim-reports-y, node-width("reports/")), (82, sim-config-y, node-width("config/"))), sim-scene-y, sim-config-y)
  for (y, label) in ((sim-scene-y, "scene/"), (sim-visibility-y, "visibility/"), (sim-detection-y, "detection/"), (sim-reports-y, "reports/"), (sim-config-y, "config/")) {
    box(82, y, label, fill: rose, edge: garnet)
  }
  let sim-scene = ((116, 91, node-width("builder.py")), (116, 89.5, node-width("object_model.py")), (116, 88, node-width("observer.py")))
  let sim-visibility = ((116, 85.5, node-width("range_filter.py")), (116, 84, node-width("land_blocking.py")), (116, 82.5, node-width("spatial_query.py")))
  let sim-detection = ((116, 80, node-width("geometry.py")), (116, 78.5, node-width("clustering.py")), (116, 77, node-width("merging.py")), (116, 75.5, node-width("noise.py")))
  let sim-reports = ((116, 73, node-width("observation.py")), (116, 71.5, node-width("truth.py")), (116, 70, node-width("schema.py")))
  let sim-config = ((116, 67.5, node-width("settings.py")), (116, 66, node-width("random_state.py")))
  connect(82, sim-scene-y, node-width("scene/"), sim-scene, 91, 88)
  connect(82, sim-visibility-y, node-width("visibility/"), sim-visibility, 85.5, 82.5)
  connect(82, sim-detection-y, node-width("detection/"), sim-detection, 80, 75.5)
  connect(82, sim-reports-y, node-width("reports/"), sim-reports, 73, 70)
  connect(82, sim-config-y, node-width("config/"), sim-config, 67.5, 66)
  for (y, label) in ((91, "builder.py"), (89.5, "object_model.py"), (88, "observer.py"), (85.5, "range_filter.py"), (84, "land_blocking.py"), (82.5, "spatial_query.py"), (80, "geometry.py"), (78.5, "clustering.py"), (77, "merging.py"), (75.5, "noise.py"), (73, "observation.py"), (71.5, "truth.py"), (70, "schema.py"), (67.5, "settings.py"), (66, "random_state.py")) {
    box(116, y, label, fill: white, edge: garnet)
  }

  // search/ uses observations and AIS inputs without importing simulator internals.
  let search-inputs-y = 59.75
  let search-candidates-y = 55
  let search-association-y = 49.5
  let search-scoring-y = 44
  let search-algorithms-y = 37
  let search-output-y = 30
  connect(48, search-y, node-width("search/"), ((82, search-inputs-y, node-width("inputs/")), (82, search-candidates-y, node-width("candidates/")), (82, search-association-y, node-width("association/")), (82, search-scoring-y, node-width("scoring/")), (82, search-algorithms-y, node-width("algorithms/")), (82, search-output-y, node-width("output/"))), search-inputs-y, search-output-y)
  for (y, label) in ((search-inputs-y, "inputs/"), (search-candidates-y, "candidates/"), (search-association-y, "association/"), (search-scoring-y, "scoring/"), (search-algorithms-y, "algorithms/"), (search-output-y, "output/")) {
    box(82, y, label, fill: blue, edge: blue-edge)
  }
  let search-inputs = ((116, 60.5, node-width("obs_io.py")), (116, 59, node-width("ais_io.py")))
  let search-candidates = ((116, 56.5, node-width("water_grid.py")), (116, 55, node-width("bounds.py")), (116, 53.5, node-width("range.py")))
  let search-association = ((116, 51, node-width("type_match.py")), (116, 49.5, node-width("bearing_gate.py")), (116, 48, node-width("assign.py")))
  let search-scoring = ((116, 45.5, node-width("likelihood.py")), (116, 44, node-width("visibility.py")), (116, 42.5, node-width("ranking.py")))
  let search-algorithms = ((116, 40, node-width("grid.py")), (116, 38.5, node-width("ransac.py")), (116, 37, node-width("branch_bound.py")), (116, 35.5, node-width("refine.py")), (116, 34, node-width("gpu_batch.py")))
  let search-output = ((116, 31.5, node-width("estimate.py")), (116, 30, node-width("confidence.py")), (116, 28.5, node-width("diagnostics.py")))
  connect(82, search-inputs-y, node-width("inputs/"), search-inputs, 60.5, 59)
  connect(82, search-candidates-y, node-width("candidates/"), search-candidates, 56.5, 53.5)
  connect(82, search-association-y, node-width("association/"), search-association, 51, 48)
  connect(82, search-scoring-y, node-width("scoring/"), search-scoring, 45.5, 42.5)
  connect(82, search-algorithms-y, node-width("algorithms/"), search-algorithms, 40, 34)
  connect(82, search-output-y, node-width("output/"), search-output, 31.5, 28.5)
  for (y, label) in ((60.5, "obs_io.py"), (59, "ais_io.py"), (56.5, "water_grid.py"), (55, "bounds.py"), (53.5, "range.py"), (51, "type_match.py"), (49.5, "bearing_gate.py"), (48, "assign.py"), (45.5, "likelihood.py"), (44, "visibility.py"), (42.5, "ranking.py"), (40, "grid.py"), (38.5, "ransac.py"), (37, "branch_bound.py"), (35.5, "refine.py"), (34, "gpu_batch.py"), (31.5, "estimate.py"), (30, "confidence.py"), (28.5, "diagnostics.py")) {
    box(116, y, label, fill: white, edge: blue-edge)
  }

  // evaluation/ alone reads simulator truth and reports accuracy and runtime.
  let eval-runner-y = 25.5
  let eval-metrics-y = 18.75
  let eval-timing-y = 11.25
  let eval-reporting-y = 3.75
  connect(48, evaluation-y, node-width("evaluation/"), ((82, eval-runner-y, node-width("runner/")), (82, eval-metrics-y, node-width("metrics/")), (82, eval-timing-y, node-width("timing/")), (82, eval-reporting-y, node-width("reporting/"))), eval-runner-y, eval-reporting-y)
  for (y, label) in ((eval-runner-y, "runner/"), (eval-metrics-y, "metrics/"), (eval-timing-y, "timing/"), (eval-reporting-y, "reporting/")) {
    box(82, y, label, fill: gray)
  }
  let eval-runner = ((116, 27, node-width("trials.py")), (116, 25.5, node-width("repeat.py")), (116, 24, node-width("runner.py")))
  let eval-metrics = ((116, 21, node-width("position_error.py")), (116, 19.5, node-width("success_rate.py")), (116, 18, node-width("feature_count.py")), (116, 16.5, node-width("association.py")))
  let eval-timing = ((116, 13.5, node-width("stage_timer.py")), (116, 12, node-width("end_to_end.py")), (116, 10.5, node-width("cpu_scale.py")), (116, 9, node-width("gpu_scale.py")))
  let eval-reporting = ((116, 6, node-width("summary.py")), (116, 4.5, node-width("tables.py")), (116, 3, node-width("figures.py")), (116, 1.5, node-width("export.py")))
  connect(82, eval-runner-y, node-width("runner/"), eval-runner, 27, 24)
  connect(82, eval-metrics-y, node-width("metrics/"), eval-metrics, 21, 16.5)
  connect(82, eval-timing-y, node-width("timing/"), eval-timing, 13.5, 9)
  connect(82, eval-reporting-y, node-width("reporting/"), eval-reporting, 6, 1.5)
  for (y, label) in ((27, "trials.py"), (25.5, "repeat.py"), (24, "runner.py"), (21, "position_error.py"), (19.5, "success_rate.py"), (18, "feature_count.py"), (16.5, "association.py"), (13.5, "stage_timer.py"), (12, "end_to_end.py"), (10.5, "cpu_scale.py"), (9, "gpu_scale.py"), (6, "summary.py"), (4.5, "tables.py"), (3, "figures.py"), (1.5, "export.py")) {
    box(116, y, label, fill: white, edge: ink)
  }
})
