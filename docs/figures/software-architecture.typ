#import "@preview/cetz:0.5.2"

#set page(width: 2900mm, height: 1200mm, margin: 18mm, fill: white)
#set text(font: "Arial", size: 10pt, fill: rgb("#000000"))

#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let garnet = rgb("#73000A")
#let green = rgb("#EAF3ED")
#let rose = rgb("#F8ECEC")
#let blue = rgb("#EAF2F7")
#let gray = rgb("#F1F3F5")
#let blue-edge = rgb("#006BA4")

#let box(cx, y, w, h, label, fill: white, edge: ink, size: 9pt) = {
  cetz.draw.rect((cx - w / 2, y), (cx + w / 2, y + h), fill: fill, stroke: (paint: edge, thickness: 1.2pt))
  cetz.draw.content((cx, y + h / 2), [#align(center)[#text(size: size, weight: "bold", fill: edge)[#label]]])
}

#let tree(parent, parent-bottom, children, child-top, bus-y) = {
  cetz.draw.line((parent, parent-bottom), (parent, bus-y), stroke: (paint: ink, thickness: 1.1pt))
  cetz.draw.line((children.at(0), bus-y), (children.at(children.len() - 1), bus-y), stroke: (paint: ink, thickness: 1.1pt))
  for child in children {
    cetz.draw.line((child, bus-y), (child, child-top), stroke: (paint: ink, thickness: 1.1pt), mark: (end: "stealth", fill: ink))
  }
}

#align(center)[
  #text(size: 32pt, weight: "bold")[Software package tree]
  #v(2mm)
  #text(size: 15pt, fill: muted)[One connected view from main.py through 59 file-level leaves. External ingestion is shown outside the software tree.]
]
#v(5mm)

#cetz.canvas({
  import cetz.draw: *

  // External source is adjacent to the trunk and is not a software package branch.
  box(112, 91, 18, 5, [External inputs], fill: gray, edge: muted, size: 12pt)
  content((112, 89), [#text(size: 9pt, fill: muted)[AIS • charts • scene and sensor settings]])
  box(138, 91, 14, 5, [main.py], fill: gray, size: 14pt)
  line((121, 93.5), (131, 93.5), stroke: (paint: garnet, thickness: 1.6pt), mark: (end: "stealth", fill: garnet))

  // Four independent top-level software branches.
  tree(138, 91, (22, 83, 163, 243), 73, 82)
  box(22, 68, 20, 5, [shared/], fill: green, edge: muted, size: 13pt)
  box(83, 68, 20, 5, [simulator/], fill: rose, edge: garnet, size: 13pt)
  box(163, 68, 20, 5, [search/], fill: blue, edge: blue-edge, size: 13pt)
  box(243, 68, 20, 5, [evaluation/], fill: gray, size: 13pt)

  // shared/ divides into neutral contracts and geospatial functions.
  tree(22, 68, (12, 32), 44, 55)
  box(12, 39, 18, 5, [contracts/], fill: green, edge: muted, size: 11pt)
  box(32, 39, 18, 5, [geospatial/], fill: green, edge: muted, size: 11pt)
  tree(12, 39, (4, 8, 12, 16, 20), 15, 27)
  tree(32, 39, (24, 28, 32, 36, 40), 15, 27)
  for (x, label) in ((4, [observation.py]), (8, [ais.py]), (12, [truth.py]), (16, [estimate.py]), (20, [validation.py]), (24, [projection.py]), (28, [land_water.py]), (32, [line_of_sight.py]), (36, [spatial_index.py]), (40, [bearing_math.py])) {
    box(x, 10, 3.5, 5, label, fill: white, edge: muted, size: 8pt)
  }

  // simulator/ owns scene construction, visibility, detection, reports, and settings.
  tree(83, 68, (55, 68, 83, 98, 109), 44, 55)
  for (x, label) in ((55, [scene/]), (68, [visibility/]), (83, [detection/]), (98, [reports/]), (109, [config/])) {
    box(x, 39, 11, 5, label, fill: rose, edge: garnet, size: 10pt)
  }
  tree(55, 39, (51, 55, 59), 15, 27)
  tree(68, 39, (64, 68, 72), 15, 27)
  tree(83, 39, (77, 81, 85, 89), 15, 27)
  tree(98, 39, (94, 98, 102), 15, 27)
  tree(109, 39, (107, 111), 15, 27)
  for (x, label) in ((51, [builder.py]), (55, [object_model.py]), (59, [observer.py]), (64, [range_filter.py]), (68, [land_blocking.py]), (72, [spatial_query.py]), (77, [geometry.py]), (81, [clustering.py]), (85, [merging.py]), (89, [noise.py]), (94, [observation.py]), (98, [truth.py]), (102, [schema.py]), (107, [settings.py]), (111, [random_state.py])) {
    box(x, 10, 3.5, 5, label, fill: white, edge: garnet, size: 8pt)
  }

  // search/ consumes observations and AIS independently of simulator internals.
  tree(163, 68, (126, 137, 150, 163, 180, 197), 44, 55)
  for (x, label) in ((126, [inputs/]), (137, [candidates/]), (150, [association/]), (163, [scoring/]), (180, [algorithms/]), (197, [output/])) {
    box(x, 39, 11, 5, label, fill: blue, edge: blue-edge, size: 10pt)
  }
  tree(126, 39, (124, 128), 15, 27)
  tree(137, 39, (133, 137, 141), 15, 27)
  tree(150, 39, (146, 150, 154), 15, 27)
  tree(163, 39, (159, 163, 167), 15, 27)
  tree(180, 39, (172, 176, 180, 184, 188), 15, 27)
  tree(197, 39, (193, 197, 201), 15, 27)
  for (x, label) in ((124, [obs_io.py]), (128, [ais_io.py]), (133, [water_grid.py]), (137, [bounds.py]), (141, [range.py]), (146, [type_match.py]), (150, [bearing_gate.py]), (154, [assign.py]), (159, [likelihood.py]), (163, [visibility.py]), (167, [ranking.py]), (172, [grid.py]), (176, [ransac.py]), (180, [branch_bound.py]), (184, [refine.py]), (188, [gpu_batch.py]), (193, [estimate.py]), (197, [confidence.py]), (201, [diagnostics.py])) {
    box(x, 10, 3.5, 5, label, fill: white, edge: blue-edge, size: 8pt)
  }

  // evaluation/ alone reads simulator truth to measure estimates and runtime.
  tree(243, 68, (217, 232, 249, 266), 44, 55)
  for (x, label) in ((217, [runner/]), (232, [metrics/]), (249, [timing/]), (266, [reporting/])) {
    box(x, 39, 13, 5, label, fill: gray, size: 10pt)
  }
  tree(217, 39, (213, 217, 221), 15, 27)
  tree(232, 39, (226, 230, 234, 238), 15, 27)
  tree(249, 39, (243, 247, 251, 255), 15, 27)
  tree(266, 39, (260, 264, 268, 272), 15, 27)
  for (x, label) in ((213, [trials.py]), (217, [repeat.py]), (221, [runner.py]), (226, [position_error.py]), (230, [success_rate.py]), (234, [feature_count.py]), (238, [association.py]), (243, [stage_timer.py]), (247, [end_to_end.py]), (251, [cpu_scale.py]), (255, [gpu_scale.py]), (260, [summary.py]), (264, [tables.py]), (268, [figures.py]), (272, [export.py])) {
    box(x, 10, 3.5, 5, label, fill: white, edge: ink, size: 8pt)
  }
})
