// Reusable technical figures. Import this module without changing page style.
// Every construction below is a calculation or code diagram, not a recorded
// geographical scene, observed adjacency graph, or pruning trajectory.
// Canvas extent is at most 276 mm by 122 mm at the default width.
#import "@preview/cetz:0.5.2": canvas, draw

#let technical-data = json("data/technical.json")
#let _ink = black
#let _muted = rgb("#444444")
#let _blue = rgb("#1f77b4")
#let _orange = rgb("#ff7f0e")
#let _green = rgb("#2ca02c")
#let _garnet = rgb("#73000A")
#let _pale = rgb("#f2f4f5")

#let _label(pos, value, size: 14pt, color: _ink, weight: "regular") = {
  draw.content(pos, text(font: "Arial", size: size, fill: color, weight: weight)[#value], anchor: "center")
}

#let _card(a, b, fill: white, stroke: _ink) = {
  draw.rect(a, b, fill: fill, stroke: (paint: stroke, thickness: 0.9pt))
}

// Filled stealth head, constructed explicitly so all arrows have the same form.
#let _arrow(a, b, color: _ink, weight: 1.1pt, head: 3.2) = {
  let dx = b.at(0) - a.at(0)
  let dy = b.at(1) - a.at(1)
  let norm = calc.sqrt(dx * dx + dy * dy)
  if norm > 0 {
    let ux = dx / norm
    let uy = dy / norm
    let half = head * 0.45
    let notch = (b.at(0) - head * 0.68 * ux, b.at(1) - head * 0.68 * uy)
    draw.line(a, notch, stroke: (paint: color, thickness: weight))
    draw.line(
      b,
      (b.at(0) - head * ux - half * uy, b.at(1) - head * uy + half * ux),
      notch,
      (b.at(0) - head * ux + half * uy, b.at(1) - head * uy - half * ux),
      close: true, fill: color, stroke: none,
    )
  }
}

#let _route(points, color: _ink) = {
  if points.len() > 2 {
    draw.line(..points.slice(0, points.len() - 1), stroke: (paint: color, thickness: 1.1pt))
  }
  _arrow(points.at(points.len() - 2), points.last(), color: color)
}

#let _figure(width, body) = canvas(length: width / 276, {
  // Invisible extent keeps all seven figures aligned in the deck.
  draw.rect((0, 0), (276, 122), stroke: none, fill: none)
  body
})

// Slide 21. Symbolic model only. No source identity is attached to a report.
#let tech-search-model(width: 276mm) = _figure(width, {
  _card((5, 57), (83, 105), fill: _pale)
  _label((44, 91), [64 positive aid bearings], size: 14pt)
  _label((44, 73), [$theta_1, dots, theta_64$], size: 24pt)
  _arrow((89, 81), (107, 81))
  _card((113, 57), (163, 105), stroke: _blue)
  _label((138, 88), [$x in C$], size: 26pt)
  _label((138, 68), [Observer cell], size: 14pt)
  _arrow((169, 81), (187, 81))
  _card((193, 57), (271, 105), fill: _pale)
  _label((232, 91), [Canonical chart objects], size: 14pt)
  _label((232, 73), [$q_1, dots, q_n$], size: 24pt)
  _label((138, 36), [$beta_(j) (x) = op("azi")_("WGS84")(x, q_j)$], size: 23pt)
  _label((138, 16), [Global bearings. Object identities are not supplied.], size: 14pt)
})

// Slide 22. All work counts are calculated, not a measured exhaustive run.
#let tech-budget(width: 276mm) = _figure(width, {
  _card((5, 34), (112, 116), fill: _pale)
  _label((58.5, 103), [$W = 1644.676 "km"$], size: 18pt)
  _label((58.5, 89), [$H = 2388.459 "km"$], size: 18pt)
  _label((58.5, 71), [$N = ceil(W / ("1 m")) ceil(H / ("1 m"))$], size: 18pt)
  _label((58.5, 50), technical-data.grid.positions_short, size: 29pt, color: _blue)
  _label((58.5, 21), [1 m grid positions], size: 14pt)
  _label((207, 113), [Ideal equal division], size: 14pt)
  for (k, y) in (88, 60, 32).enumerate() {
    _card((148, y - 10), (271, y + 10), stroke: _blue)
    draw.rect((148, y - 10), (181, y + 10), fill: _blue, stroke: none)
    _label((164.5, y), [GPU #k], size: 16pt, color: white)
    _label((226, y), technical-data.grid.per_device_short, size: 23pt)
    _route(((118, 71), (135, 71), (135, y), (146, y)))
  }
  _label((138, 5), [Calculated counts only. No exhaustive-search runtime was measured.], size: 14pt, color: _muted)
})

// Slide 23 supplement. All three panels are mathematical constructions.
// The terminal panel is magnified, not a sampled or recorded survivor path.
#let tech-refinement(width: 276mm) = _figure(width, {
  let starts = (12, 110, 214)
  for (k, x) in starts.enumerate() {
    _card((x, 55), (x + 46, 101), stroke: _blue)
    if k == 1 {
      draw.line((x + 23, 55), (x + 23, 101), stroke: 0.9pt + _blue)
      draw.line((x, 78), (x + 46, 78), stroke: 0.9pt + _blue)
    }
  }
  _label((35, 113), [$k = 0$], size: 18pt)
  _label((133, 113), [$k = 1$], size: 18pt)
  _label((237, 113), [$k = 14$], size: 18pt)
  _arrow((65, 78), (102, 78))
  _label((84, 94), [1 halving], size: 14pt)
  _arrow((164, 78), (206, 78))
  _label((185, 94), [13 more], size: 14pt)
  _label((35, 44), [10,000 m], size: 17pt)
  _label((133, 44), [5,000 m], size: 17pt)
  _label((237, 44), [0.6103515625 m], size: 17pt)
  _label((237, 67), [Magnified], size: 14pt, color: _muted)
  _label((138, 25), [$h_k = 10000 / 2^k "m"$], size: 23pt)
  _label((138, 7), [Mathematical subdivision. Every retained nonterminal cell is split into four.], size: 14pt, color: _muted)
})

// Slide 24. These are proof conditions from the code, not sampled rejections.
#let tech-pruning(width: 276mm) = _figure(width, {
  _label((138, 112), [Every valid explanation in the cell must remain a possible edge.], size: 16pt)
  _label((138, 96), [Certified failure rejects. Unsupported bounds retain.], size: 14pt, color: _muted)
  _card((5, 52), (134, 85))
  _label((69.5, 75), [Whole cell is known land], size: 14pt)
  _label((69.5, 60), [$C subset.eq L_("known")$], size: 21pt)
  _card((142, 52), (271, 85))
  _label((206.5, 75), [One report has no object], size: 14pt)
  _label((206.5, 60), [$exists i: op("deg")_(C) (i) = 0$], size: 21pt)
  _card((5, 13), (134, 46))
  _label((69.5, 36), [Too few distinct possibilities], size: 14pt)
  _label((69.5, 21), [$n_C < 64$], size: 21pt)
  _card((142, 13), (271, 46), stroke: _garnet)
  _label((206.5, 36), [No simultaneous injection], size: 14pt)
  _label((206.5, 21), [$nu(E_C) < 64$], size: 21pt, color: _garnet)
  _label((138, 4), [Whole-cell tests use positive reports. Unseen objects are not penalties.], size: 14pt, color: _muted)
})

// Slide 25 supplement. No adjacency is drawn without a saved real witness.
#let tech-distinct-proof(width: 276mm) = _figure(width, {
  _card((5, 58), (91, 111), fill: _pale)
  _label((48, 96), [64 distinct detections], size: 16pt)
  _label((48, 76), [$theta_1, dots, theta_64$], size: 25pt)
  _arrow((95, 84), (109, 84))
  _card((114, 61), (162, 107), stroke: _blue)
  _label((138, 91), [$pi$], size: 30pt)
  _label((138, 73), [One-to-one], size: 14pt)
  _arrow((167, 84), (181, 84))
  _card((186, 58), (271, 111), fill: _pale)
  _label((228.5, 96), [Possible chart objects], size: 16pt)
  _label((228.5, 76), [$q_1, dots, q_(n_C)$], size: 25pt)
  _label((138, 43), [$i != k => pi(i) != pi(k)$], size: 23pt)
  _label((138, 23), [$abs(N_(C) (S)) < abs(S) => nu(E_C) < 64$], size: 23pt)
  _label((138, 6), [General condition. The recorded rejection witness is shown on slide 25.], size: 14pt, color: _muted)
})

// Residual scoring is distinct from the conservative whole-cell support graph.
#let tech-residual(width: 276mm) = _figure(width, {
  _label((138, 110), [$r_(i j) (x) = abs(op("wrap")_(180)(theta_i - beta_(j) (x)))$], size: 24pt)
  _card((5, 38), (133, 91), stroke: _green)
  _label((69, 81), [64 injective exact matches], size: 16pt)
  _label((69, 64), [$r_(i pi(i)) (x) < epsilon_i - delta_("num")$], size: 19pt)
  _label((69, 49), [Known water and selected visibility], size: 14pt)
  _label((69, 41), [Flags = 0], size: 14pt)
  _card((143, 38), (271, 91), stroke: _blue)
  _label((207, 81), [Select by summed residual], size: 16pt)
  _label((207, 64), [$J(x) = sum_(i=1)^64 r_(i pi(i)) (x)$], size: 21pt)
  _label((207, 46), [$hat(x) = op("argmin")_(x in X_("consistent")) J(x)$], size: 19pt)
  _label((138, 23), [$J(hat(x)) = 6.2809796 "°"$], size: 25pt)
  _label((138, 7), [Current frozen estimate. Total residual over 64 positive aid reports.], size: 14pt, color: _muted)
})

// Slide 28. Exact current-run accounting; regions occur at different scales.
#let tech-workload(width: 276mm) = _figure(width, {
  _card((5, 89), (85, 119))
  _label((45, 109), technical-data.current.root_cells_display, size: 24pt)
  _label((45, 96), [Initial cells], size: 14pt)
  _arrow((91, 104), (105, 104))
  _card((111, 89), (195, 119), stroke: _blue)
  _label((153, 109), technical-data.current.evaluated_regions_display, size: 24pt)
  _label((153, 96), [Evaluated regions], size: 14pt)
  _route(((153, 87), (153, 76), (45, 76), (45, 66)))
  _arrow((138, 76), (138, 66))
  _route(((153, 87), (153, 76), (237, 76), (237, 66)))
  for (x, number, label, color) in (
    (45, technical-data.current.rejected_regions_display, [Rejected], _orange),
    (138, technical-data.current.expanded_regions_display, [Expanded], _blue),
    (236, technical-data.current.terminal_centers_display, [Terminal centers], _green),
  ) {
    _card((x - 39, 33), (x + 39, 64), stroke: color)
    _label((x, 54), number, size: 24pt, color: color)
    _label((x, 40), label, size: 14pt)
  }
  _label((138, 21), [$58118 + 6360 + 397 = 64875$], size: 20pt)
  _label((138, 6), [$39435 + 4 times 6360 = 64875$], size: 20pt)
})
