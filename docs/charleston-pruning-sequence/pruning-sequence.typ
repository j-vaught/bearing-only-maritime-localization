#import "@preview/cetz:0.5.2": canvas, draw
#set page(width: 260mm, height: 265mm, margin: 10mm, fill: white)
#set text(font: "DejaVu Sans", size: 10pt, fill: black)
#let data = json("stages.json")
#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#let water = rgb("#f3f7fa")
#let land = rgb("#b6bec4")
#let w = 230
#let h = 184
#for (i, stage) in data.frames.enumerate() [
  #text(size: 18pt, weight: "bold")[#stage.title]
  #linebreak()
  #text(size: 9pt)[#stage.subtitle]
  #v(3mm)
  #canvas(length: 1mm, {
    import draw: *
    rect((0, 0), (w, h), fill: water, stroke: 0.5pt + black)
    for polygon in stage.land {
      for (r, ring) in polygon.enumerate() {
        let fill-color = if r == 0 { land } else { water }
        line(..ring.map(p => (p.at(0) * w, p.at(1) * h)), close: true, fill: fill-color, stroke: none)
      }
    }
    for r in stage.removed {
      rect((r.at(0) * w, r.at(1) * h), (r.at(2) * w, r.at(3) * h), fill: orange.transparentize(50%), stroke: none)
    }
    for r in stage.kept {
      rect((r.at(0) * w, r.at(1) * h), (r.at(2) * w, r.at(3) * h), fill: blue.transparentize(40%), stroke: none)
    }
    for r in stage.at("consistent", default: ()) {
      rect((r.at(0) * w, r.at(1) * h), (r.at(2) * w, r.at(3) * h), fill: rgb("#2ca02c"), stroke: 0.2pt + black)
    }
    for label in stage.labels {
      content((label.at(1) * w + 1, label.at(2) * h), box(fill: white, inset: 0.5mm)[#text(size: 8pt)[#label.at(0)]], anchor: "west")
    }
    content((w - 1, h - 1), box(fill: white, inset: 1mm)[N], anchor: "north-east")
    line((5, 5), (w / 5 + 5, 5), stroke: 1.2pt + black)
    line((5, 4), (5, 6), stroke: 0.7pt + black)
    line((w / 5 + 5, 4), (w / 5 + 5, 6), stroke: 0.7pt + black)
    let distance = stage.width_km / 5
    let label = if distance >= 1 { str(calc.round(distance, digits: 1)) + " km" } else { str(calc.round(distance * 1000, digits: 1)) + " m" }
    content((5, 8), box(fill: white, inset: 0.5mm)[#text(size: 8pt)[#label]], anchor: "west")
  })
  #v(2mm)
  #grid(columns: (auto, 1fr, auto, 1fr), column-gutter: 2mm,
    rect(width: 4mm, height: 3mm, fill: blue, stroke: none), [#if "consistent" in stage { [Unresolved centers] } else { [Retained cells] }],
    rect(width: 4mm, height: 3mm, fill: orange, stroke: none), [Rejected at this stage],
  )
  #if "consistent" in stage [
    #rect(width: 4mm, height: 3mm, fill: rgb("#2ca02c"), stroke: none) Consistent centers
  ]
  #v(1mm)
  #text(size: 8pt)[GPU pruning replay · Exact terminal-cell parity with saved search · Cells smaller than a display pixel enlarged.]
  #linebreak()
  #text(size: 8pt)[#if "score_note" in stage { stage.score_note } else { [Zoom includes every retained region. Cell counts can increase through subdivision.] }]
  #if i < data.frames.len() - 1 { pagebreak() }
]
