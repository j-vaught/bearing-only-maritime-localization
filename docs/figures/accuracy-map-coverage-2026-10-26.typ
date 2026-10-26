#import "@preview/lilaq:0.6.0" as lq

#set page(width: 270mm, height: 160mm, margin: 8mm, fill: white)
#set text(size: 8pt)

#let data = json("accuracy-map-coverage-2026-10-26.json")
#let old = data.at("previous_port_only")
#let current = data.at("map_v3_candidate")
#let old-unknown = old.at("unknown_fraction_percent")
#let current-unknown = current.at("unknown_fraction_percent")

#align(center)[
  #text(size: 13pt, weight: "bold")[Chart-derived coverage and port detail]
  #linebreak()
  #text(size: 9pt)[Equal-area comparison over the same Eastern Seaboard bounding box. Snapshot dated 2026-10-26.]
]

#v(4mm)
#grid(
  columns: (1.45fr, 1fr),
  column-gutter: 8mm,
  lq.diagram(
    title: [Known area and unknown area],
    xlabel: [Fraction of bounding-box domain (%)],
    xlim: (0, 100),
    ylim: (0.4, 2.6),
    xaxis: (ticks: ((0, [0]), (20, [20]), (40, [40]), (60, [60]), (80, [80]), (100, [100])), subticks: none),
    yaxis: (ticks: ((1, [Previous port-only MapV2]), (2, [MapV3 candidate])), subticks: none),
    width: 154mm,
    height: 82mm,
    lq.hbar(
      (old.at("known_fraction_percent"), current.at("known_fraction_percent")),
      (1, 2),
      label: [Known],
    ),
    lq.hbar(
      (old-unknown, current-unknown),
      (1, 2),
      base: (old.at("known_fraction_percent"), current.at("known_fraction_percent")),
      label: [Unknown],
    ),
  ),
  lq.diagram(
    title: [Harbour detail union. 0.3103% of domain, nested in known area],
    xlabel: [Union area as fraction of domain (%)],
    xlim: (0, 0.5),
    ylim: (0.4, 1.6),
    xaxis: (ticks: ((0, [0]), (0.1, [0.1]), (0.2, [0.2]), (0.3, [0.3]), (0.4, [0.4]), (0.5, [0.5])), subticks: none),
    yaxis: (ticks: ((1, [20 Harbour maps]),), subticks: none),
    width: 88mm,
    height: 82mm,
    lq.hbar((current.at("fine_port_union_fraction_percent"),), (1,)),
  ),
)

#v(3mm)
#text(size: 7pt)[
  The previous MapV2 has 0.482% known port-only area and 99.518% unknown. The MapV3 candidate has 76.260% known bbox area and 23.740% unknown over a 3,383,691.331 km² domain. Unknown remains explicitly unknown. The 20-map Harbour union covers 10,500.268 km², or 0.3103% of the domain, wholly within coarse known coverage; it is overlaid detail and does not add to the known-area total. All 24 port maps were byte-verified. No Approach or Berthing maps contribute fine detail.
]

#v(2mm)
#text(size: 7pt, weight: "bold")[
  The validated MapV3 candidate is approved as a new versioned benchmark input; the existing default remains MapV2. Coverage is chart-derived and partial; the map does not establish navigable depth, current shoreline conditions, or field accuracy.
]
