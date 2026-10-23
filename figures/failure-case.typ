#import "@preview/cetz:0.5.2"

#set page(width: 180mm, height: 145mm, margin: 5mm, fill: white)
#set text(size: 8pt)

#let garnet = rgb("#73000A")
#let ink = rgb("#000000")
#let gray = rgb("#9AA1A8")
#let pale = rgb("#E3E6E9")
#let blue = rgb("#006BA4")
#let orange = rgb("#D55E00")
#let object-rows = csv("data/failure_landmarks.csv").slice(1)
#let pose = csv("data/failure_pose.csv").at(1)
#let sx(value) = float(value) / 5

#align(center)[
  #text(size: 13pt, weight: "bold")[A failed 10 km localization matched the wrong vessels]
  #v(1mm)
  #text(size: 8pt)[
    Trial #pose.at(0): #pose.at(5) km position error; #pose.at(7) detections; #pose.at(9)/#pose.at(10) correct AIS matches.
  ]
  #v(2mm)
  #cetz.canvas({
    import cetz.draw: *

    // 40 km by 40 km synthetic map. Each plotted unit represents 5 km.
    for tick in range(-4, 5, step: 2) {
      line((tick, -4), (tick, 4), stroke: (paint: pale, thickness: 0.45pt))
      line((-4, tick), (4, tick), stroke: (paint: pale, thickness: 0.45pt))
      content((tick, -4.35), [#str(tick * 5)])
      content((-4.4, tick), [#str(tick * 5)])
    }
    rect((-4, -4), (4, 4), stroke: (paint: ink, thickness: 0.8pt), fill: none)

    // Show the true observer's 10 km visible disc and the estimator's 10 km candidate disc.
    circle((sx(pose.at(1)), sx(pose.at(2))), radius: 2, stroke: (paint: garnet, thickness: 1.1pt), fill: none)
    circle((sx(pose.at(3)), sx(pose.at(4))), radius: 2, stroke: (paint: blue, thickness: 1.1pt, dash: "dashed"), fill: none)

    // Plot the complete synthetic landmark catalogue, then emphasize truth-visible and assigned points.
    for row in object-rows {
      let point = (sx(row.at(1)), sx(row.at(2)))
      if row.at(6) == "True" {
        circle(point, radius: 0.075, fill: blue, stroke: none)
      } else if row.at(5) == "True" and row.at(4) == "True" {
        circle(point, radius: 0.065, fill: garnet, stroke: none)
      } else if row.at(5) == "True" {
        circle(point, radius: 0.065, fill: orange, stroke: none)
      } else {
        circle(point, radius: 0.045, fill: gray, stroke: none)
      }
    }

    // Mark truth and the false global solution.
    circle((sx(pose.at(1)), sx(pose.at(2))), radius: 0.11, fill: ink, stroke: (paint: white, thickness: 0.6pt))
    rect((sx(pose.at(3)) - 0.11, sx(pose.at(4)) - 0.11), (sx(pose.at(3)) + 0.11, sx(pose.at(4)) + 0.11), fill: blue, stroke: (paint: white, thickness: 0.6pt))
    line((sx(pose.at(1)), sx(pose.at(2))), (sx(pose.at(3)), sx(pose.at(4))), stroke: (paint: ink, thickness: 0.8pt), mark: (end: "stealth", fill: ink))
    content((sx(pose.at(1)) - 0.6, sx(pose.at(2)) - 0.35), [true observer])
    content((sx(pose.at(3)) + 0.9, sx(pose.at(4)) + 0.2), [estimated observer])

    // Coordinate axes point east and north. Arrowheads are filled stealth marks.
    line((-3.5, -3.3), (-2.5, -3.3), stroke: (paint: ink, thickness: 0.8pt), mark: (end: "stealth", fill: ink))
    line((-3.5, -3.3), (-3.5, -2.3), stroke: (paint: ink, thickness: 0.8pt), mark: (end: "stealth", fill: ink))
    content((-2.35, -3.3), [E])
    content((-3.5, -2.15), [N])
  })

  #v(2mm)
  #text(size: 8pt)[
    #box(fill: garnet, width: 2.3mm, height: 2.3mm) #h(1mm) Reported AIS
    #h(3mm) #box(fill: orange, width: 2.3mm, height: 2.3mm) #h(1mm) Reported non-AIS
    #h(3mm) #box(fill: blue, width: 2.3mm, height: 2.3mm) #h(1mm) Wrong candidate matches
    #h(3mm) #box(fill: gray, width: 2.3mm, height: 2.3mm) #h(1mm) Other catalog objects
  ]
  #v(2mm)
  #align(center)[#text(size: 8pt)[Solid and dashed circles are the 10 km visibility limits at the true and estimated positions. Axes use local east/north coordinates in km.]]
]
