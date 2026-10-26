#import "@preview/lilaq:0.6.0" as lq

#set page(width: 297mm, height: 210mm, margin: 9mm, fill: white)
#set text(size: 9pt)
#let d = json("development-scene-coverage-2026-10-26.json")
#let ranges = d.at("ranges")
#let supported = ranges.map(r => r.at("supported"))
#let unsupported = ranges.map(r => r.at("unsupported"))
#let strata = d.at("known_association_geometry_strata_supported_unique_scenes")
#let sumfmt(value) = str(value)

#align(center)[
  #text(size: 17pt, weight: "bold")[Development scene support and geometry]
  #linebreak()
  #text(size: 9pt)[Frozen development dataset · 2026-10-26]
]
#v(2mm)
#align(center)[#text(size: 10pt, weight: "bold")[Dataset support and known-association geometry only. These counts are not search or localization accuracy.]]
#v(2mm)
#text(size: 11pt, weight: "bold")[Supported and unsupported physical scenes by nominal range]
#text(size: 8pt)[Each range has 600 physical scene pairs. Report-type on/off rows describe paired views of the same scenes and are counted once here.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Nominal physical scenes (n = 600 per range)],
    xlabel: [Physical scene count],
    xlim: (0, 600),
    ylim: (0.4, 6.6),
    xaxis: (ticks: ((0, [0]), (150, [150]), (300, [300]), (450, [450]), (600, [600])), subticks: none),
    yaxis: (ticks: ((1, [10 km unsupported]), (2, [10 km supported]), (3, [5 km unsupported]), (4, [5 km supported]), (5, [1 km unsupported]), (6, [1 km supported])), subticks: none),
    width: 250mm,
    height: 58mm,
    lq.hbar(unsupported, (5, 3, 1)),
    lq.hbar(supported, (6, 4, 2)),
  )
]
#align(center)[#text(size: 8pt)[Unsupported. 1 km 331 · 5 km 162 · 10 km 152.  Supported. 1 km 269 · 5 km 438 · 10 km 448.]]
#v(2mm)
#text(size: 11pt, weight: "bold")[Known-association geometry among supported physical scenes]
#text(size: 8pt)[95% major-radius strata. Denominator is 1,155 supported physical scenes, counting each paired on/off scene once.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Supported-scene geometry strata (n = 1,155)],
    xlabel: [Supported scene count],
    xlim: (0, 1100),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (250, [250]), (500, [500]), (750, [750]), (1000, [1,000])), subticks: none),
    yaxis: (ticks: ((1, [Singular]), (2, [>100 m]), (3, [25–100 m]), (4, [≤25 m])), subticks: none),
    width: 250mm,
    height: 43mm,
    lq.hbar((strata.at("singular"),), (1,)),
    lq.hbar((strata.at(">100m"),), (2,)),
    lq.hbar((strata.at("25-100m"),), (3,)),
    lq.hbar((strata.at("<=25m"),), (4,)),
  )
]
#align(center)[#text(size: 8pt)[≤25 m. #sumfmt(strata.at("<=25m")).  25–100 m. #sumfmt(strata.at("25-100m")).  >100 m. #sumfmt(strata.at(">100m")).  Singular. #sumfmt(strata.at("singular")).]]
#v(2mm)
#text(size: 7.5pt)[
  All 1,800 paired physical scenes are retained; 1,155 are supported and 645 unsupported. Geometry strata are descriptive only and do not establish an oracle error bound. No search outputs, held-out inputs, or validation outcomes were read. Source results/accuracy/development-information-summary-2026-10-26.json, commit fcac843526862253ea5f8cbd9d09b448ac927fee, SHA-256 0d59b959bcf8c482dba0db9c62dd0f0fee4d6dba761d64afc76dcc8b0d6e15ba. Authorship J.C. Vaught.
]
