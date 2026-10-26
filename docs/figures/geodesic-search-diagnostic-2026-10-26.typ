#import "@preview/lilaq:0.6.0" as lq
#set page(width: 350mm, height: 380mm, margin: 10mm, fill: white)
#set text(size: 8pt)
#let d = json("geodesic-search-diagnostic-2026-10-26.json")
#let runs = d.at("runs")
#let y = (1, 2, 3)
#let top = runs.map(r => r.at("top_error_m"))
#let nearest = runs.map(r => r.at("nearest_error_m"))
#let wall = runs.map(r => r.at("elapsed_wall_s"))
#let obj = d.at("objective_diagnostic")
#align(center)[
  #text(size: 15pt, weight: "bold")[Full-coast search diagnostics]
  #linebreak()
  #text(size: 9pt)[Three failed searches on one frozen five-report development case. Snapshot 2026-10-26.]
]
#v(2mm)
#text(size: 10pt, weight: "bold")[Top-ranked estimate error]
#text(size: 7pt)[CRS-correct WGS84 geodesic distance to evaluator truth. Lower is closer; none was accepted.]
#lq.diagram(
  xlabel: [Top-ranked position error (m)], xlim: (0, 2000000), ylim: (0.4, 3.6),
  xaxis: (ticks: ((0,[0]),(500000,[500k]),(1000000,[1,000k]),(1500000,[1,500k]),(2000000,[2,000k])), subticks: none),
  yaxis: (ticks: ((1,[Pairwise]),(2,[Planar wedge]),(3,[Geodesic wedge])), subticks: none),
  width: 290mm, height: 38mm,
  lq.hbar(top, y),
)
#linebreak()
#text(size: 7pt)[Pairwise 509,653.742 m. Planar wedge 1,898,447.237 m. Geodesic wedge 548,327.465 m.]
#v(2mm)
#text(size: 10pt, weight: "bold")[Nearest saved candidate error]
#text(size: 7pt)[Minimum geodesic error among 7, 260, and 261 saved hypotheses, respectively.]
#lq.diagram(
  xlabel: [Nearest saved candidate error (m)], xlim: (0, 600000), ylim: (0.4, 3.6),
  xaxis: (ticks: ((0,[0]),(100000,[100k]),(200000,[200k]),(300000,[300k]),(400000,[400k]),(500000,[500k]),(600000,[600k])), subticks: none),
  yaxis: (ticks: ((1,[Pairwise]),(2,[Planar wedge]),(3,[Geodesic wedge])), subticks: none),
  width: 290mm, height: 38mm,
  lq.hbar(nearest, y),
)
#linebreak()
#text(size: 7pt)[Pairwise 506,055.789 m, minimum of 7. Planar wedge 1,897.057 m, minimum of 260. Geodesic wedge 128,757.746 m, minimum of 261.]
#v(2mm)
#text(size: 10pt, weight: "bold")[External run wall time]
#text(size: 7pt)[Separate diagnostic runs and timing scopes. This is not a controlled runtime comparison.]
#lq.diagram(
  xlabel: [External wall time (s)], xlim: (0, 800), ylim: (0.4, 3.6),
  xaxis: (ticks: ((0,[0]),(200,[200]),(400,[400]),(600,[600]),(800,[800])), subticks: none),
  yaxis: (ticks: ((1,[Pairwise]),(2,[Planar wedge]),(3,[Geodesic wedge])), subticks: none),
  width: 290mm, height: 38mm,
  lq.hbar(wall, y),
)
#linebreak()
#text(size: 7pt)[Pairwise 48 s. Planar wedge 227.053 s external one-shot. Geodesic wedge 706.479 s external cold one-shot.]
#v(2mm)
#text(size: 10pt, weight: "bold")[Post-search objective audit]
#text(size: 7pt)[Evaluator-only scoring. The truth position was opened only after search completed. Lower objective is better.]
#lq.diagram(
  xlabel: [Objective score], xlim: (0, 26), ylim: (0.4, 4.6),
  xaxis: (ticks: ((0,[0]),(5,[5]),(10,[10]),(15,[15]),(20,[20]),(25,[25])), subticks: none),
  yaxis: (ticks: ((1,[Truth position]),(2,[Neighbor water cell]),(3,[Containing water cell]),(4,[Returned top candidate])), subticks: none),
  width: 290mm, height: 43mm,
  lq.hbar((obj.at("truth_score"),), (1,)),
  lq.hbar((obj.at("neighbor_water_cell_score"),), (2,)),
  lq.hbar((obj.at("containing_cell_score"),), (3,)),
  lq.hbar((obj.at("returned_top_candidate_score"),), (4,)),
)
#linebreak()
#text(size: 7pt)[Truth #str(obj.at("truth_score")). Neighbor cell #str(obj.at("neighbor_water_cell_score")). Truth-containing cell #str(obj.at("containing_cell_score")). Returned top candidate #str(obj.at("returned_top_candidate_score")).]
#v(2mm)
#table(
  columns: (1.4fr, 1fr, 1.4fr, 1.4fr, 1fr, 1fr),
  inset: 3pt, stroke: 0.5pt + black,
  [Run], [Source pairs], [Generated cells], [Candidate cap], [Saved], [Accepted],
  [Pairwise], [not reported], [not reported], [64], [7], [No],
  [Planar wedge], [333,340 / 333,340], [1,530,303], [256], [260], [No],
  [Geodesic wedge], [333,340 / 333,340], [1,457,546], [256], [261], [No],
)
#v(1mm)
#text(size: 7pt)[
  The frozen geodesic run recorded 209 failed solver samples among 83,001,660. A separate later solver audit converged all 209 after tightening the internal iteration target; it did not alter the frozen run. Although all source pairs were visited, generation completeness was false and the frozen search status was failed. Both wedge runs exhausted the exact-scoring budget and remained ambiguous.
]
#text(size: 7pt, weight: "bold")[
  The truth-containing 250 m cell was generated and classified as water. Its mean normalized residual was 0.23667468, while the last selected proposal had 0.02674343, leaving the cell outside the top-256 exact-scoring set. The neighboring water cell scored 16.355641148, the containing cell 17.849166741, and the returned top candidate 23.786192787. This shows why an angle-window-only explanation is insufficient: the known cell existed but residual-based ordering omitted it under the candidate cap. Truth was used only after search.
]
#v(1mm)
#text(size: 6.5pt)[
  Provenance and input hashes are stored in adjacent JSON. The frozen geodesic run is commit #d.at("provenance").at("frozen_search_commit"). A35 evaluator diagnostic SHA-256 #d.at("provenance").at("a35_diagnostic_sha256"). The geodesic-run evaluator diagnostic SHA-256 #d.at("provenance").at("geodesic_evaluator_sha256").
]
#align(right)[#text(size: 7pt)[J.C. Vaught]]
