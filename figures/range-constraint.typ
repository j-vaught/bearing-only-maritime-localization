#import "@preview/cetz:0.5.2"

#set page(width: 190mm, height: 99mm, margin: 5mm, fill: white)
#set text(size: 9pt)

#let garnet = rgb("#73000A")
#let ink = rgb("#000000")
#let muted = rgb("#68717A")
#let blue = rgb("#006BA4")
#let pale = rgb("#D8DDE2")

#align(center)[
  #text(size: 14pt, weight: "bold")[A visibility limit filters AIS candidates by distance]
  #v(2mm)
  #cetz.canvas({
    import cetz.draw: *

    // Shared global north reference.
    line((0.2, -0.4), (0.2, 1.2), stroke: (paint: ink, thickness: 0.8pt), mark: (end: "stealth", fill: ink))
    content((0.2, 1.45), [N / 0°])

    // Finite-radius candidate neighborhood.
    content((4.2, 3.1), [Finite visibility: R = 10 km])
    circle((4.2, 0), radius: 2, stroke: (paint: garnet, thickness: 1.2pt), fill: none)
    circle((4.2, 0), radius: 0.08, fill: ink, stroke: none)
    content((4.2, -0.35), [candidate ship])

    // In-range object at 7 km and global bearing 30 degrees.
    line((4.2, 0), (4.9, 1.212), stroke: (paint: ink, thickness: 0.7pt), mark: (end: "stealth", fill: ink))
    circle((4.9, 1.212), radius: 0.09, fill: garnet, stroke: none)
    content((5.95, 1.55), [AIS object])
    content((5.95, 1.22), [7 km, 30°])

    // In-range object at 9 km and global bearing 130 degrees.
    line((4.2, 0), (5.578, -1.157), stroke: (paint: ink, thickness: 0.7pt), mark: (end: "stealth", fill: ink))
    circle((5.578, -1.157), radius: 0.09, fill: garnet, stroke: none)
    content((6.45, -1.35), [9 km, 130°])

    // Out-of-range object at 14 km and global bearing 70 degrees.
    line((4.2, 0), (6.832, 0.957), stroke: (paint: muted, thickness: 0.7pt, dash: "dashed"), mark: (end: "stealth", fill: muted))
    circle((6.832, 0.957), radius: 0.09, fill: pale, stroke: (paint: muted, thickness: 0.7pt))
    content((7.45, 0.76), [14 km, excluded])

    // Infinite-range candidate neighborhood.
    content((13.3, 3.1), [Infinite visibility])
    circle((13.3, 0), radius: 0.08, fill: ink, stroke: none)
    content((13.3, -0.35), [candidate ship])
    line((13.3, 0), (14.0, 1.212), stroke: (paint: ink, thickness: 0.7pt), mark: (end: "stealth", fill: ink))
    circle((14.0, 1.212), radius: 0.09, fill: garnet, stroke: none)
    line((13.3, 0), (14.678, -1.157), stroke: (paint: ink, thickness: 0.7pt), mark: (end: "stealth", fill: ink))
    circle((14.678, -1.157), radius: 0.09, fill: garnet, stroke: none)
    line((13.3, 0), (15.932, 0.957), stroke: (paint: blue, thickness: 0.7pt), mark: (end: "stealth", fill: blue))
    circle((15.932, 0.957), radius: 0.09, fill: blue, stroke: none)
    content((15.8, 1.55), [all AIS records remain candidates])
    content((15.8, 1.22), [including distant objects])

    line((9.6, -1.7), (9.6, 2.6), stroke: (paint: pale, thickness: 0.8pt))
    content((4.2, -2.5), [The range gate removes distant matches; reported bearings still span 360°.])
    content((13.4, -2.5), [Without a range limit, more identities compete in each association.])
  })
]
