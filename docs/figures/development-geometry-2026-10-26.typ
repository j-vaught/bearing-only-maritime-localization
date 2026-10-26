#import "@preview/lilaq:0.6.0" as lq

#set page(width: 290mm, height: 185mm, margin: 10mm, fill: white)
#set text(size: 8pt)

#let data = json("development-geometry-2026-10-26.json")
#let ranges = data.at("ranges")
#let y = (1, 2, 3)
#let strong = ranges.map(row => row.at("strata").at("<=25m"))
#let moderate = ranges.map(row => row.at("strata").at("25-100m"))
#let weak = ranges.map(row => row.at("strata").at(">100m"))
#let moderate_end = strong.zip(moderate).map(pair => pair.at(0) + pair.at(1))
#let weak_end = moderate_end.zip(weak).map(pair => pair.at(0) + pair.at(1))
#let weak-base = strong.zip(moderate).map(pair => pair.at(0) + pair.at(1))

#align(center)[
  #text(size: 14pt, weight: "bold")[Development known-association geometry]
  #linebreak()
  #text(size: 9pt)[Supported physical scenes by range and 95% ellipse major-radius stratum. Snapshot dated 2026-10-26.]
]

#v(3mm)
#align(center)[
  #lq.diagram(
    title: [Supported scenes. Paired type-on/off inputs counted once per physical scene],
    xlabel: [Supported physical scenes, partitioned by geometry stratum],
    xlim: (0, 500),
    ylim: (0.4, 3.6),
    xaxis: (ticks: ((0,[0]),(100,[100]),(200,[200]),(300,[300]),(400,[400]),(500,[500])), subticks: none),
    yaxis: (ticks: ((1,[1 km]),(2,[5 km]),(3,[10 km])), subticks: none),
    width: 235mm,
    height: 78mm,
    lq.hbar(strong, y, label: [<=25 m]),
    lq.hbar(moderate_end, y, base: strong, label: [25-100 m]),
    lq.hbar(weak_end, y, base: moderate_end, label: [>100 m]),
  )
]

#v(3mm)
#align(center)[
  #table(
    columns: (1fr, 1.25fr, 1fr, 1fr, 1fr, 1fr, 1.35fr),
    inset: 4pt,
    stroke: 0.5pt + black,
    [Range], [Supported / 600], [<=25 m], [25-100 m], [>100 m], [Singular], [Median matched features],
    ..ranges.map(row => (
      [#str(row.at("range_km")) km],
      [#str(row.at("supported_scenes"))],
      [#str(row.at("strata").at("<=25m"))],
      [#str(row.at("strata").at("25-100m"))],
      [#str(row.at("strata").at(">100m"))],
      [#str(row.at("strata").at("singular"))],
      [#str(row.at("median_catalog_matches"))],
    )).flatten(),
  )
]

#v(2mm)
#text(size: 7.5pt)[
  Geometry uses known associations and bearing Fisher information, with a 0.5 deg sigma fallback and 0.05 deg floor. The 95% major radius is singular when geometry is not full rank. Support requires valid reference water and at least three detected postmerge catalog-matched physical groups. Each range has 600 paired type-on/off rows from the same physical scenes; geometry, support, strata, and medians match exactly, so plotted counts use one physical set rather than adding pairs as independent scenes.
]

#v(1mm)
#text(size: 7pt, weight: "bold")[
  Descriptive development geometry only. These known-association diagnostics are not search outcomes or localization accuracy. Held-out rows and search outputs were not read.
]

#text(size: 6.5pt)[
  Source: results/accuracy/development-difficulty-2026-10-26.json, SHA-256 #data.at("source_file_sha256"). Source eligibility and preparation hashes are preserved in adjacent JSON.
]
