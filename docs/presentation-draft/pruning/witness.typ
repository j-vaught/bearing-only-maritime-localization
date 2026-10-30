#import "@preview/cetz:0.5.2": canvas, draw

#set page(width: 276mm, height: 128mm, margin: 0mm, fill: white)
#set text(font: "Arial", size: 14pt, fill: black)
#set document(author: "J.C. Vaught", title: "Recorded distinct-marker pruning witness")
#let data = json("witness.json")
#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#let pale = rgb("#f2f4f5")
#let label(p, value, size: 14pt, color: black, weight: "regular", anchor: "center") = {
  draw.content(p, text(size: size, fill: color, weight: weight)[#value], anchor: anchor)
}
#let box(a, b, fill: white, color: black) = draw.rect(a, b, fill: fill, stroke: 0.9pt + color)
#let y-position(i, n) = if n == 1 { 68 } else { 105 - i * 77 / (n - 1) }

#canvas(length: 1mm, {
  import draw: *
  rect((0, 0), (276, 128), fill: white, stroke: none)
  let fig = data.figure
  let rows = fig.display_report_indices
  let columns = fig.display_neighbor_indices
  let rx = 27
  let ox = 132
  label((rx, 119), [Positive reports], weight: "bold")
  label((ox, 119), [Possible objects], weight: "bold")
  if fig.display_graph {
    // The incidence is the complete recorded adjacency for these reports.
    for edge in data.edges {
      if edge.report_index in rows and edge.object_index in columns {
        let i = rows.position(value => value == edge.report_index)
        let j = columns.position(value => value == edge.object_index)
        line((rx + 9, y-position(i, rows.len())), (ox - 9, y-position(j, columns.len())),
          stroke: 0.9pt + rgb("#555555"), mark: (end: "stealth", fill: rgb("#555555")))
      }
    }
    for (i, row) in rows.enumerate() {
      let y = y-position(i, rows.len())
      box((rx - 10, y - 3.8), (rx + 10, y + 3.8), fill: white, color: blue)
      label((rx, y), [r#row], color: blue)
    }
    for (j, column) in columns.enumerate() {
      let y = y-position(j, columns.len())
      box((ox - 9, y - 3.8), (ox + 9, y + 3.8), fill: orange, color: black)
      label((ox, y), [q#column])
    }
  } else {
    label((79, 91), [Adjacency retained in witness.json], size: 16pt)
    label((79, 73), [#data.edge_count recorded edges], size: 22pt)
    label((79, 55), [All neighboring objects included], size: 14pt)
  }
  box((158, 55), (271, 111), fill: pale)
  label((214.5, 98), [#data.report_count reports], size: 25pt, color: blue)
  label((214.5, 82), [#data.neighbor_count possible objects], size: 25pt)
  label((214.5, 64), [$abs(N(S)) < abs(S)$], size: 24pt)
  label((214.5, 38), [No distinct assignment exists.], size: 16pt)
  label((214.5, 27), [Reject the entire candidate cell.], size: 16pt, weight: "bold")
  if fig.mode == "full_graph" {
    label((79, 15), [All neighbors and edges for the report subset.], size: 14pt)
  } else {
    label((79, 15), [Subset view. Full witness counts shown at right.], size: 14pt)
  }
  label((138, 4), [Recorded GPU #data.source_metadata.device graph · #data.source_metadata.object_count candidate objects · flags 0], size: 14pt)
})
