#import "plot-common.typ": *
#set page(width: 320mm, height: 180mm, margin: 0mm, fill: white)
#set text(font: "Arial", size: 9pt)
#set document(author: "J.C. Vaught", title: "Preloaded GPU search results for approved B sites")
#let data = json("data.json")
#let rows = data.rows.filter(r => r.status == "completed")
#let titles = (
  "time-error": [Search time versus position error],
  "time-aids": [Search time versus detected charting aids],
  "error-aids": [Position error versus detected charting aids],
)
#let point(p) = {
  let (x, y) = p
  draw.rect((x - 0.75, y - 0.75), (x + 0.75, y + 0.75), fill: blue, stroke: 0.25pt + white)
}
#let annotated(p, label, d) = {
  let q = (p.at(0) + d.at(0), p.at(1) + d.at(1))
  draw.line(p, q, stroke: 0.3pt + rgb("#888888"))
  draw.content(q, text(size: 8pt)[#label])
}
#let render(plot) = place(top + left, canvas(length: 1mm, {
  import draw: *
  rect((0, 0), (320, 180), fill: white, stroke: none)
  content((160, 172), text(size: 15pt)[#titles.at(plot)])
  content((160, 162), text(size: 9pt)[20 completed coast-wide searches · GPUs 0–2 · Map already loaded])
  if plot == "time-aids" {
    let a = axes((20, 42), (125, 90), (15, 105), (0.5, 400), (20, 40, 60, 80, 100), (0.5, 1, 3, 10, 30, 100, 300), [Detected charting aids], [Search time (s), logarithmic], logy: true)
    let b = axes((178, 42), (125, 90), (15, 105), (0, 8), (20, 40, 60, 80, 100), (0, 2, 4, 6, 8), [Detected charting aids], [Search time (s)])
    a.drawing; b.drawing
    content((82, 150), text(size: 9pt)[Full range · 20 sites])
    content((240, 150), text(size: 9pt)[Detail · 16 sites with search time ≤ 8 s])
    let labels = (B11: (7, 1), B16: (7, 0), B5: (6, 2), B7: (-6, 2))
    let detail = (
      B2: (-1, 7), B4: (12, -8), B6: (0, 7), B8: (8, 6),
      B9: (12, -2), B12: (-2, 4), B13: (1, 7), B14: (0, 7),
      B15: (1, -7), B17: (6, 8), B18: (1, 7), B19: (-5, 6),
      B20: (2, -6), B21: (-1, -8), B22: (7, 2), B24: (-2, 8),
    )
    for r in rows {
      let p = ((a.x)(r.aids), (a.y)(r.search_s))
      point(p)
      if r.site in labels { annotated(p, r.site, labels.at(r.site)) }
      if r.search_s <= 8 {
        let q = ((b.x)(r.aids), (b.y)(r.search_s))
        point(q)
        annotated(q, r.site, detail.at(r.site))
      }
    }
  } else if plot == "time-error" {
    let a = axes((20, 42), (125, 90), (0.1, 30), (0.5, 400), (0.1, 0.3, 1, 3, 10, 30), (0.5, 1, 3, 10, 30, 100, 300), [Position error (m), logarithmic], [Search time (s), logarithmic], logx: true, logy: true)
    let b = axes((178, 42), (125, 90), (0, 4), (0, 8), (0, 1, 2, 3, 4), (0, 2, 4, 6, 8), [Position error (m)], [Search time (s)])
    a.drawing; b.drawing
    content((82, 150), text(size: 9pt)[Full range · 20 sites])
    content((240, 150), text(size: 9pt)[Detail · 15 sites within 4 m and 8 s])
    let labels = (B11: (7, 1), B16: (7, 0), B5: (5, 3), B7: (5, 0), B18: (0, 6))
    let detail = (
      B2: (1, 7), B4: (-2, -7), B6: (4, 6), B8: (-2, 7),
      B9: (6, 8), B12: (4, 3), B13: (6, 8), B14: (7, -7),
      B15: (-4, -7), B17: (6, -7), B19: (-5, 6), B20: (6, -4),
      B21: (-1, 14), B22: (5, 6), B24: (-2, 7),
    )
    for r in rows {
      let p = ((a.x)(r.error_m), (a.y)(r.search_s))
      point(p)
      if r.site in labels { annotated(p, r.site, labels.at(r.site)) }
      if r.error_m <= 4 and r.search_s <= 8 {
        let q = ((b.x)(r.error_m), (b.y)(r.search_s))
        point(q)
        annotated(q, r.site, detail.at(r.site))
      }
    }
  } else {
    let a = axes((25, 42), (270, 90), (15, 105), (0.1, 30), (20, 40, 60, 80, 100), (0.1, 0.3, 1, 3, 10, 30), [Detected charting aids], [Position error (m), logarithmic; lower is better], logy: true)
    a.drawing
    let labels = (
      B2: (-3, 6), B4: (0, 5), B5: (0, 5), B6: (3, 5),
      B7: (-4, 5), B8: (-1, -5), B9: (5, -6), B11: (-1, 6),
      B12: (-2, 5), B13: (0, 5), B14: (0, 5), B15: (-4, 5),
      B16: (-2, 5), B17: (0, 8), B18: (0, 5), B19: (-4, -5),
      B20: (-3, 5), B21: (0, -5), B22: (3, 4), B24: (-4, 5),
    )
    for r in rows {
      let p = ((a.x)(r.aids), (a.y)(r.error_m))
      point(p)
      annotated(p, r.site, labels.at(r.site))
    }
  }
  content((160, 22), text(size: 9pt)[Position error is measured in meters; lower is better.])
  content((160, 14), text(size: 8pt)[B1, B3, B10 and B23 remain excluded by the approved charting-aid count filter.])
  content((160, 5), text(size: 8pt)[Search timings exclude input loading, GPU preload, simulation and final selection.])
}))
#let selected = sys.inputs.at("plot", default: "all")
#if selected == "all" {
  for plot in titles.keys() {
    render(plot)
    pagebreak(weak: true)
  }
} else {
  render(selected)
}
