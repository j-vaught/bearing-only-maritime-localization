#import "@preview/lilaq:0.6.0" as lq

#set page(width: 285mm, height: 350mm, margin: 9mm, fill: white)
#set text(size: 8pt)
#let data = json("cached-search-progress-2026-10-26.json")
#let scale = data.at("exact_scaling")
#let small = data.at("initial_255_sample")
#let fail = data.at("startup_failure")

#align(center)[
  #text(size: 15pt, weight: "bold")[Cached candidate scoring progress]
  #linebreak()
  #text(size: 9pt)[Exact scoring profiles on shared-host cache samples. Captured 2026-10-26.]
]
#v(2mm)
#align(center)[#text(size: 8pt, weight: "bold")[Diagnostic measurements only. No full-domain localization success or full-coast accuracy result.]]
#v(3mm)

#text(size: 10pt, weight: "bold")[2,048-candidate exact sample]
#text(size: 7.5pt)[Startup and steady request stages are shown separately. All completed runs evaluated the same 2,048 selected candidates.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Worker startup],
    xlabel: [Startup time (s)],
    xlim: (0, 32),
    ylim: (0.4, 3.6),
    xaxis: (ticks: ((0, [0]), (10, [10]), (20, [20]), (30, [30])), subticks: none),
    yaxis: (ticks: ((1, [1 worker]), (2, [4 workers]), (3, [8 workers])), subticks: none),
    width: 250mm,
    height: 48mm,
    lq.hbar(scale.map(row => row.at("startup_s")), (1, 2, 3), label: [Startup]),
  )
]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Steady request stage],
    xlabel: [Request time (s)],
    xlim: (0, 56),
    ylim: (0.4, 3.6),
    xaxis: (ticks: ((0, [0]), (10, [10]), (20, [20]), (30, [30]), (40, [40]), (50, [50])), subticks: none),
    yaxis: (ticks: ((1, [1 worker]), (2, [4 workers]), (3, [8 workers])), subticks: none),
    width: 250mm,
    height: 48mm,
    lq.hbar(scale.map(row => row.at("request_s")), (1, 2, 3), label: [Request]),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1fr, 1.1fr, 1.1fr, 1.2fr, 1.4fr),
    inset: 2.5pt,
    stroke: 0.5pt + black,
    [Workers], [Candidates], [Exact scored], [Best objective], [Best candidate ID],
    ..scale.map(row => (
      [#str(row.at("workers"))],
      [#str(row.at("candidate_count"))],
      [#str(row.at("exact_scored"))],
      [#str(calc.round(row.at("best_score") * 1000000) / 1000000)],
      [#row.at("tie_ids").at(0)],
    )).flatten(),
  )
]
#v(1mm)
#align(center)[#text(size: 7.5pt)[The 1, 4, and 8 worker exact runs share best objective #str(calc.round(scale.at(0).at("best_score") * 1000000) / 1000000) and tie ID #scale.at(0).at("tie_ids").at(0). The 16-worker run timed out during worker startup after 53.073 s and dispatched 0 candidates.]]

#v(3mm)
#text(size: 10pt, weight: "bold")[Initial 255-candidate exact versus screened sample]
#text(size: 7.5pt)[Wall time for the fixed initial cache-order sample. Exact mode evaluates all candidates; screened mode may prune candidates.]
#v(1mm)
#align(center)[
  #lq.diagram(
    title: [Wall time by mode and worker count],
    xlabel: [Wall time (s)],
    xlim: (0, 130),
    ylim: (0.4, 4.6),
    xaxis: (ticks: ((0, [0]), (20, [20]), (40, [40]), (60, [60]), (80, [80]), (100, [100]), (120, [120])), subticks: none),
    yaxis: (ticks: ((1, [1 worker exact]), (2, [1 worker screened]), (3, [4 workers exact]), (4, [4 workers screened])), subticks: none),
    width: 250mm,
    height: 55mm,
    lq.hbar(small.map(row => row.at("wall_s")), (1, 2, 3, 4), label: [Mode]),
  )
]
#v(1mm)
#align(center)[
  #table(
    columns: (1fr, 1.3fr, 1fr, 1fr, 1.4fr),
    inset: 2.5pt,
    stroke: 0.5pt + black,
    [Run], [Wall time (s)], [Exact scored], [Pruned], [Best candidate ID],
    ..small.map(row => (
      [#str(row.at("workers")) worker #str(row.at("mode"))],
      [#str(calc.round(row.at("wall_s") * 1000) / 1000)],
      [#str(row.at("exact_scored"))],
      [#str(row.at("pruned"))],
      [#row.at("tie_ids").at(0)],
    )).flatten(),
  )
]
#v(1mm)
#align(center)[#text(size: 7.5pt)[All four runs retained best objective #str(calc.round(small.at(0).at("best_score") * 1000000) / 1000000) and tie ID #small.at(0).at("tie_ids").at(0). Screened runs pruned 82 candidates at 1 worker and 75 at 4 workers.]]

#v(3mm)
#line(length: 100%, stroke: 0.6pt + black)
#v(1mm)
#text(size: 7pt)[
  Both workloads use diagnostic samples from a frozen 1,457,546-cell proposal cache with the same exact objective. Timings were measured on a busy shared host and are not controlled latency or release-throughput measurements. The 2,048-candidate workload is an evenly spaced cache sample; the initial 255-candidate workload is cache-order sampled. Sampled best candidates do not establish full-domain correctness or localization accuracy. Source report SHA-256: #data.at("source_report_sha256"). Scaling JSON SHA-256: #data.at("scaling_source_sha256"). Initial profile JSON SHA-256: #data.at("initial_profile_source_sha256"). Authorship J.C. Vaught.
]
