#import "@preview/lilaq:0.6.0" as lq
#set page(width: 340mm, height: 300mm, margin: 10mm, fill: white)
#set text(size: 8pt)
#let d = json("search-repair-checkpoint-2026-10-26.json")
#let baseline = d.at("matched_baseline")
#let local = baseline.at("local")
#let coast = baseline.at("coast")
#let los = d.at("los_component")
#let wedge = d.at("wedge_coverage")
#let search = d.at("unresolved_search")
#align(center)[
  #text(size: 15pt, weight: "bold")[Search repair checkpoint]
  #linebreak()
  #text(size: 9pt)[Verified historical and component evidence. Snapshot 2026-10-26.]
]
#v(2mm)
#text(size: 10pt, weight: "bold")[Matched baseline errors]
#text(size: 7pt)[Same input fingerprints on main and integration. Position error from known position.]
#lq.diagram(
  xlabel: [Position error (m)], xlim: (0, 100), ylim: (0.4, 2.6),
  xaxis: (ticks: ((0,[0]),(25,[25]),(50,[50]),(75,[75]),(100,[100])), subticks: none),
  yaxis: (ticks: ((1,[Local]),(2,[Coast])), subticks: none),
  width: 275mm, height: 43mm,
  lq.hbar((local.at("main_error_m"),), (1,)),
  lq.hbar((coast.at("main_error_m"),), (2,)),
)
#linebreak()
#text(size: 7pt)[Local #str(local.at("main_error_m")) m in both branches. Coast #str(coast.at("main_error_m")) m in both branches.]
#v(2mm)
#text(size: 10pt, weight: "bold")[Exact line-of-sight component]
#text(size: 7pt)[909 identical rays with identical status vectors. Scalar median versus batched crop median.]
#lq.diagram(
  xlabel: [Elapsed time (s)], xlim: (0, 12), ylim: (0.4, 2.6),
  xaxis: (ticks: ((0,[0]),(3,[3]),(6,[6]),(9,[9]),(12,[12])), subticks: none),
  yaxis: (ticks: ((1,[Scalar]),(2,[Batch + crop])), subticks: none),
  width: 275mm, height: 43mm,
  lq.hbar((los.at("scalar_median_s"),), (1,)),
  lq.hbar((los.at("batch_crop_median_s"),), (2,)),
)
#linebreak()
#text(size: 7pt)[Scalar #str(los.at("scalar_median_s")) s. Batch plus crop #str(los.at("batch_crop_median_s")) s, with crop creation included. Statuses: 635 blocked, 274 clear, 0 unknown. This is fixture-level LOS time, not localization latency.]
#v(2mm)
#text(size: 10pt, weight: "bold")[Bearing-wedge source coverage]
#text(size: 7pt)[Processed source pairs out of #str(wedge.at("source_pairs_total")).]
#lq.diagram(
  xlabel: [Processed source pairs], xlim: (0, 333340), ylim: (0.4, 2.6),
  xaxis: (ticks: ((0,[0]),(100000,[100k]),(200000,[200k]),(300000,[300k])), subticks: none),
  yaxis: (ticks: ((1,[Legacy budget]),(2,[Complete pass])), subticks: none),
  width: 275mm, height: 43mm,
  lq.hbar((wedge.at("legacy_processed"),), (1,)),
  lq.hbar((wedge.at("complete_processed"),), (2,)),
)
#linebreak()
#text(size: 7pt)[Legacy budget processed #str(wedge.at("legacy_processed")) pairs. Complete pass processed #str(wedge.at("complete_processed")) pairs into #str(wedge.at("coarse_cells")) coarse 10 km cells. #str(wedge.at("voting_only_s")) s voting time only; excludes scoring and map evaluation.]
#v(2mm)
#text(size: 10pt, weight: "bold")[Unresolved full-search case]
#text(size: 7pt)[Pairwise search attempt. Error to known position for top-ranked hypothesis and minimum among seven hypotheses.]
#lq.diagram(
  xlabel: [Position error (m)], xlim: (0, 450000), ylim: (0.4, 2.6),
  xaxis: (ticks: ((0,[0]),(100000,[100k]),(200000,[200k]),(300000,[300k]),(400000,[400k])), subticks: none),
  yaxis: (ticks: ((1,[Best of 7]),(2,[Top ranked])), subticks: none),
  width: 275mm, height: 43mm,
  lq.hbar((search.at("minimum_error_among_7_hypotheses_m"),), (1,)),
  lq.hbar((search.at("top_ranked_error_m"),), (2,)),
)
#linebreak()
#text(size: 7pt)[Best of seven #str(search.at("minimum_error_among_7_hypotheses_m")) m. Top ranked #str(search.at("top_ranked_error_m")) m. 48 s wall. Candidate budget #str(search.at("candidate_budget_used")) / #str(search.at("candidate_budget_total")). Status budget exhausted; decision ambiguous; exact-key matches #str(search.at("exact_key_matches")) / #str(search.at("exact_key_total")).]
#v(2mm)
#text(size: 6.5pt)[
  Source reports and input SHA-256 values are recorded in adjacent JSON. Baseline report #baseline.at("source_report_sha256"). LOS and search report #los.at("source_report_sha256"). Wedge report #wedge.at("source_report_sha256").
]
#text(size: 7pt, weight: "bold")[
  These panels report separate measurements and workloads. The matched baseline compares identical inputs only. LOS timing is component-level, wedge timing covers voting only, and the full-search case remains unresolved. No held-out accuracy or end-to-end speed claim is made.
]
#align(right)[#text(size: 7pt)[J.C. Vaught]]
