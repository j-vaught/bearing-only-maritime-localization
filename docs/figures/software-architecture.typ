#import "@preview/cetz:0.5.2"

#set page(width: 440mm, height: 297mm, margin: 12mm, fill: white)
#set text(font: "Arial", size: 9pt, fill: rgb("#000000"))

#let ink = rgb("#000000")
#let muted = rgb("#53606A")
#let garnet = rgb("#73000A")
#let green = rgb("#EAF3ED")
#let rose = rgb("#F8ECEC")
#let blue = rgb("#EAF2F7")
#let gray = rgb("#F1F3F5")

#let box(cx, y, w, h, label, fill: white, edge: ink, size: 8pt) = {
  cetz.draw.rect((cx - w / 2, y), (cx + w / 2, y + h), fill: fill, stroke: (paint: edge, thickness: 1pt))
  cetz.draw.content((cx, y + h / 2), [#align(center)[#text(size: size, weight: "bold", fill: edge)[#label]]])
}

#let tree(parent, parent_bottom, children, child_top, bus_y) = {
  cetz.draw.line((parent, parent_bottom), (parent, bus_y), stroke: (paint: ink, thickness: 0.9pt))
  cetz.draw.line((children.at(0), bus_y), (children.at(children.len() - 1), bus_y), stroke: (paint: ink, thickness: 0.9pt))
  for child in children {
    cetz.draw.line((child, bus_y), (child, child_top), stroke: (paint: ink, thickness: 0.9pt), mark: (end: "stealth", fill: ink))
  }
}

#let titled_page(title, subtitle) = align(center)[
  #text(size: 22pt, weight: "bold")[#title]
  #v(2mm)
  #text(size: 11pt, fill: muted)[#subtitle]
]

#titled_page([Software package tree], [Boxes are software modules. External ingestion is outside the package; every package node connects to its parent.])
#v(5mm)

#cetz.canvas({
  import cetz.draw: *

  // Outside source connects to the main software entry point.
  box(5.2, 24.2, 8.8, 1.5, [External ingestion / providers], fill: gray, edge: muted, size: 9pt)
  box(19.0, 24.2, 7.2, 1.5, [main.py], fill: gray, size: 10pt)
  content((5.2, 23.4), [#text(size: 8pt, fill: muted)[AIS • charts • scene / sensor settings]])
  line((9.7, 24.95), (15.3, 24.95), stroke: (paint: garnet, thickness: 1.2pt), mark: (end: "stealth", fill: garnet))

  // Main package branches.
  tree(19.0, 24.2, (4.2, 13.4, 26.0, 36.0), 19.3, 21.0)
  box(4.2, 17.9, 7.0, 1.4, [shared/], fill: green, edge: muted, size: 10pt)
  box(13.4, 17.9, 7.0, 1.4, [simulator/], fill: rose, edge: garnet, size: 10pt)
  box(26.0, 17.9, 7.0, 1.4, [search/], fill: blue, edge: rgb("#006BA4"), size: 10pt)
  box(36.0, 17.9, 7.0, 1.4, [evaluation/], fill: gray, size: 10pt)

  // Recursive package branches.
  tree(4.2, 17.9, (2.5, 5.9), 14.2, 15.8)
  box(2.5, 12.8, 3.8, 1.4, [contracts/], fill: green, edge: muted)
  box(5.9, 12.8, 4.0, 1.4, [geospatial/], fill: green, edge: muted)

  tree(13.4, 17.9, (10.0, 13.4, 16.8), 14.2, 15.8)
  box(10.0, 12.8, 3.7, 1.4, [scene/], fill: rose, edge: garnet)
  box(13.4, 12.8, 3.7, 1.4, [sensing/], fill: rose, edge: garnet)
  box(16.8, 12.8, 3.7, 1.4, [reports/], fill: rose, edge: garnet)

  tree(26.0, 17.9, (21.5, 24.5, 27.5, 30.5), 14.2, 15.8)
  box(21.5, 12.8, 4.4, 1.4, [candidates/], fill: blue, edge: rgb("#006BA4"), size: 7.5pt)
  box(24.5, 12.8, 4.4, 1.4, [association/], fill: blue, edge: rgb("#006BA4"), size: 7.5pt)
  box(27.5, 12.8, 4.4, 1.4, [scoring/], fill: blue, edge: rgb("#006BA4"), size: 7.5pt)
  box(30.5, 12.8, 4.4, 1.4, [algorithms/], fill: blue, edge: rgb("#006BA4"), size: 7.5pt)

  tree(36.0, 17.9, (34.0, 38.0), 14.2, 15.8)
  box(34.0, 12.8, 3.8, 1.4, [metrics/], fill: gray)
  box(38.0, 12.8, 3.8, 1.4, [timing + reporting/], fill: gray, size: 7pt)

  content((20.0, 10.8), [#align(center)[#text(size: 10pt, weight: "bold")[No simulator ↔ search imports. Both may call shared/ contracts and geospatial APIs.]]])
})

#pagebreak()
#titled_page([shared/ expanded], [Neutral contracts and geometry. This branch is the only reusable software dependency between simulator and search.])
#v(5mm)
#cetz.canvas({
  import cetz.draw: *
  box(20, 24, 7, 1.4, [shared/], fill: green, edge: muted, size: 10pt)
  tree(20, 24, (10, 30), 20.2, 22.0)
  box(10, 18.8, 7, 1.4, [contracts/], fill: green, edge: muted)
  box(30, 18.8, 7, 1.4, [geospatial/], fill: green, edge: muted)

  tree(10, 18.8, (2, 6, 10, 14, 18), 14.8, 16.5)
  tree(30, 18.8, (22, 26, 30, 34, 38), 14.8, 16.5)
  box(2, 13.4, 3.5, 1.4, [observation.py], fill: white, edge: muted, size: 7pt)
  box(6, 13.4, 3.5, 1.4, [ais.py], fill: white, edge: muted)
  box(10, 13.4, 3.5, 1.4, [truth.py], fill: white, edge: muted)
  box(14, 13.4, 3.5, 1.4, [estimate.py], fill: white, edge: muted, size: 7pt)
  box(18, 13.4, 3.5, 1.4, [validation.py], fill: white, edge: muted, size: 7pt)

  box(22, 13.4, 3.5, 1.4, [projection.py], fill: white, edge: muted, size: 7pt)
  box(26, 13.4, 3.5, 1.4, [land_water.py], fill: white, edge: muted, size: 7pt)
  box(30, 13.4, 3.5, 1.4, [line_of_sight.py], fill: white, edge: muted, size: 6.7pt)
  box(34, 13.4, 3.5, 1.4, [spatial_index.py], fill: white, edge: muted, size: 6.7pt)
  box(38, 13.4, 3.5, 1.4, [bearing_math.py], fill: white, edge: muted, size: 6.7pt)
})

#pagebreak()
#titled_page([simulator/ expanded], [Scene construction, range and land visibility, angular detection, and separate measurement / truth reports.])
#v(5mm)
#cetz.canvas({
  import cetz.draw: *
  box(20, 24, 7, 1.4, [simulator/], fill: rose, edge: garnet, size: 10pt)
  tree(20, 24, (4, 11, 18, 25, 35), 20.2, 22.0)
  box(4, 18.8, 5.6, 1.4, [scene/], fill: rose, edge: garnet)
  box(11, 18.8, 5.6, 1.4, [visibility/], fill: rose, edge: garnet)
  box(18, 18.8, 5.6, 1.4, [detection/], fill: rose, edge: garnet)
  box(25, 18.8, 5.6, 1.4, [reports/], fill: rose, edge: garnet)
  box(35, 18.8, 5.6, 1.4, [config/], fill: rose, edge: garnet)

  tree(4, 18.8, (2, 4, 6), 14.8, 16.5)
  tree(11, 18.8, (9, 11, 13), 14.8, 16.5)
  tree(18, 18.8, (15, 17, 19, 21), 14.8, 16.5)
  tree(25, 18.8, (23, 25, 27), 14.8, 16.5)
  tree(35, 18.8, (33, 37), 14.8, 16.5)

  for (x, label) in ((2, [builder.py]), (4, [object_model.py]), (6, [observer.py]), (9, [range_filter.py]), (11, [land_blocking.py]), (13, [spatial_query.py]), (15, [geometry.py]), (17, [clustering.py]), (19, [merging.py]), (21, [noise.py]), (23, [observation.py]), (25, [truth.py]), (27, [schema.py]), (33, [settings.py]), (37, [random_state.py])) {
    box(x, 13.4, 1.85, 1.4, label, fill: white, edge: garnet, size: 6.8pt)
  }
})

#pagebreak()
#titled_page([search/ expanded], [Candidate generation, object association, scoring, and swappable search algorithms. All feature nodes are separate and connected to their parent.])
#v(5mm)
#cetz.canvas({
  import cetz.draw: *
  box(20, 24, 7, 1.4, [search/], fill: blue, edge: rgb("#006BA4"), size: 10pt)
  tree(20, 24, (3, 9, 15, 21, 29, 37), 20.2, 22.0)
  box(3, 18.8, 4.8, 1.4, [inputs/], fill: blue, edge: rgb("#006BA4"))
  box(9, 18.8, 4.8, 1.4, [candidates/], fill: blue, edge: rgb("#006BA4"))
  box(15, 18.8, 4.8, 1.4, [association/], fill: blue, edge: rgb("#006BA4"))
  box(21, 18.8, 4.8, 1.4, [scoring/], fill: blue, edge: rgb("#006BA4"))
  box(29, 18.8, 4.8, 1.4, [algorithms/], fill: blue, edge: rgb("#006BA4"))
  box(37, 18.8, 4.8, 1.4, [output/], fill: blue, edge: rgb("#006BA4"))

  tree(3, 18.8, (2, 4), 14.8, 16.5)
  tree(9, 18.8, (7, 9, 11), 14.8, 16.5)
  tree(15, 18.8, (13, 15, 17), 14.8, 16.5)
  tree(21, 18.8, (19, 21, 23), 14.8, 16.5)
  tree(29, 18.8, (25, 27, 29, 31, 33), 14.8, 16.5)
  tree(37, 18.8, (35, 37, 39), 14.8, 16.5)

  for (x, label) in ((2, [obs_io.py]), (4, [ais_io.py]), (7, [water_grid.py]), (9, [bounds.py]), (11, [range.py]), (13, [type_match.py]), (15, [bearing_gate.py]), (17, [assign.py]), (19, [likelihood.py]), (21, [visibility.py]), (23, [ranking.py]), (25, [grid.py]), (27, [ransac.py]), (29, [branch_bound.py]), (31, [refine.py]), (33, [gpu_batch.py]), (35, [estimate.py]), (37, [confidence.py]), (39, [diagnostics.py])) {
    box(x, 13.4, 1.8, 1.4, label, fill: white, edge: rgb("#006BA4"), size: 6.8pt)
  }
})

#pagebreak()
#titled_page([evaluation/ expanded], [The evaluator receives estimated pose and simulator truth, and reports localization accuracy, feature requirements, and runtime.])
#v(5mm)
#cetz.canvas({
  import cetz.draw: *
  box(20, 24, 7, 1.4, [evaluation/], fill: gray, size: 10pt)
  tree(20, 24, (3.5, 12, 20.5, 29), 20.2, 22.0)
  box(3.5, 18.8, 5.3, 1.4, [runner/], fill: gray)
  box(12, 18.8, 5.3, 1.4, [metrics/], fill: gray)
  box(20.5, 18.8, 5.3, 1.4, [timing/], fill: gray)
  box(29, 18.8, 5.3, 1.4, [reporting/], fill: gray)

  tree(3.5, 18.8, (2, 4, 6), 14.8, 16.5)
  tree(12, 18.8, (8, 10, 12, 14), 14.8, 16.5)
  tree(20.5, 18.8, (17, 19, 21, 23), 14.8, 16.5)
  tree(29, 18.8, (26, 28, 30, 32), 14.8, 16.5)

  for (x, label) in ((2, [trials.py]), (4, [repeat.py]), (6, [runner.py]), (8, [position_error.py]), (10, [success_rate.py]), (12, [feature_count.py]), (14, [association.py]), (17, [stage_timer.py]), (19, [end_to_end.py]), (21, [cpu_scale.py]), (23, [gpu_scale.py]), (26, [summary.py]), (28, [tables.py]), (30, [figures.py]), (32, [export.py])) {
    box(x, 13.4, 1.85, 1.4, label, fill: white, edge: ink, size: 6.8pt)
  }
})
