#import "@preview/lilaq:0.6.0" as lq
#set page(width: 280mm, height: 160mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("a45-map-diagnostic-2026-10-26.json")
#let startup = data.at("startup")
#let query = data.at("repeated_predicate_query")
#let counts = query.at("classifications")
#align(center)[
  #text(size: 13pt, weight: "bold")[Exact vector-map acceleration diagnostic]
  #linebreak()
  #text(size: 9pt)[Fixed-map CPU timings. 2026-10-26.]
]
#v(4mm)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 8mm,
  lq.diagram(
    title: [Map construction. 11.148 and 11.361 to 4.562 and 4.600 s],
    xlabel: [Cold initialization time (s)],
    xlim: (0, 12),
    ylim: (0.3, 5.7),
    xaxis: (ticks: ((0,[0]),(3,[3]),(6,[6]),(9,[9]),(12,[12])), subticks: none),
    yaxis: (ticks: ((1,[Baseline run 1]),(2,[Baseline run 2]),(4,[Optimized run 1]),(5,[Optimized run 2])), subticks: none),
    width: 108mm, height: 76mm,
    lq.hbar(startup.at("baseline_s"), (1, 2)),
    lq.hbar(startup.at("optimized_s"), (4, 5)),
  ),
  lq.diagram(
    title: [20,000 point queries. 11.308 s to 0.195 s],
    xlabel: [Elapsed time for 20,000 classifications (s)],
    xlim: (0, 12),
    ylim: (0.3, 2.7),
    xaxis: (ticks: ((0,[0]),(3,[3]),(6,[6]),(9,[9]),(12,[12])), subticks: none),
    yaxis: (ticks: ((1,[Baseline]),(2,[Prepared predicates])), subticks: none),
    width: 108mm, height: 76mm,
    lq.hbar((query.at("baseline_s"),), (1,)),
    lq.hbar((query.at("optimized_s"),), (2,)),
  ),
)
#v(3mm)
#align(center)[
 #table(
  columns: (1fr,1fr,1fr,1fr), inset: 3pt, stroke: 0.5pt + black,
  [Query classifications], [Before], [After], [Match],
  [Water], [#str(counts.at("water"))], [#str(counts.at("water"))], [Identical],
  [Land], [#str(counts.at("land"))], [#str(counts.at("land"))], [Identical],
  [Unknown], [#str(counts.at("unknown"))], [#str(counts.at("unknown"))], [Identical],
 )
]
#v(2mm)
#text(size: 7pt)[
 Source: A45 exact-map profile report at commit 859e85c. Both cold construction runs produced 7,163 land polygon parts. Query classifications were unchanged while elapsed time changed from 11.308 s to 0.195 s.
]
#text(size: 7pt, weight: "bold")[
 These isolated timings do not measure end-to-end localization latency, search accuracy, or field performance. Both runs used identical map bytes and EPSG:32618.
]