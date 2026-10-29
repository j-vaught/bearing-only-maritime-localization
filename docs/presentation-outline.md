# Bearing-only maritime localization. Presentation plan

Prepared for a 45–60 minute technical review. Written by J.C. Vaught.

This document proposes the presentation and its figure production. It does not create the PowerPoint, change the implementation, or authorize new searches. Additional recordings are included as proposed work. The presentation describes the current implementation and current saved results, without literature comparisons or comparisons against earlier implementation versions.

## The presentation contract

Use Charleston as the recurring example. Begin with the localization problem, introduce globally referenced bearings, establish the actual geographic inputs, explain how observations are generated, then develop the search and finish with its recorded result and timing. Keep geographic landmarks, symbols, north orientation and object colors consistent as the scale changes.

The main deck should contain approximately 36 slides, with 8 technical backup slides. Target approximately 50 minutes of delivery, with discussion bringing the review toward one hour. Shorten to 45 minutes by moving slides 3, 12, 13 and 35 into backup. These are narrative subdivisions rather than 36 unrelated topics. Several slides should be successive builds of the same geographic scene.

Every geographic position, shoreline, catalog object, recorded candidate, measurement and execution count must come from a named source. Visibility, pruning and result illustrations must use the exact projected map recorded in that run’s settings, rather than replacing it with a live service or a different geographic export. The presentation may also contain mathematical constructions and calculations explicitly identified as such. Do not create arbitrary points, invented sensor measurements, decorative vessel photographs, placeholder performance bars or synthetic map textures.

Real geographic inputs and simulated observations are separate evidence classes. The current result uses real NOAA geographic data, archived AIS positions and simulated bearings. It is not an onboard sensor trial. Small figure captions such as “Simulated observations on NOAA geometry” are sufficient on the relevant slides; the full explanation belongs in speaker notes.

The opening range example is a sensitivity calculation on the actual Charleston geometry, not a comparison to prior work. Do not attribute a failure to a published method or present an unimplemented ranging estimator as an existing baseline. Similarly, the proposed AIS-plus-aids evaluation must not appear as a completed experiment.

All authored maps, quantitative plots and diagrams will use Typst with CeTZ/Lilaq. Generate PDF/SVG/PNG figures and assemble animations from those rendered frames. PowerPoint titles, numerical labels and notes remain editable. Use default high-contrast plotting colors, square corners and filled stealth arrows. Garnet may serve as a restrained emphasis color. Source website screenshots are documentary captures; they do not replace quantitative maps.

## Verified numbers available for the deck

| Quantity | Exact meaning | Figure use |
|---|---|---|
| 64,400 | Source chart component rows, including cross-scale repeats. | Source aggregation. Never label these unique physical aids. |
| 32,168 | Canonical exact-coordinate chart locations. | Main map count. |
| 31,925 + 243 | Known physical-aid locations plus locations retaining unknown classification. | Notes or preprocessing detail. |
| 2,268 | AIS-source vessel records in the selected one-minute snapshot. | Main AIS count. Do not substitute the illustrative number 300. |
| 34,436 | Canonical chart locations plus AIS-source records. | Searchable geographic catalog count. |
| 24 | Configured B observation locations, recorded as water against the stored map. | Geographic scenario coverage, not 24 completed accuracy experiments. |
| 200 → 80 → 79 → 69 + 1 → 70 | In-range physical objects, clear visible objects, angular groups, detected groups plus extra vessel report, final reports in the saved Charleston frame. | Simulator funnel. |
| 64 + 6 | Chart-aid and vessel reports in that simulated frame. | Observation display. Only the 64 aid bearings constrain the current resident solver. |
| 360°, 10 km | Sensor coverage and range limit. | Sensor setup. |
| 0.1° | Angular grouping resolution. | Object-merging example. |
| 1/6°; ±0.5° | Underlying Gaussian standard deviation and hard error bound. | Noise figure. The noise is rejection-sampled inside the bound. |
| 0.9; 1 | Detection probability and expected Poisson vessel-clutter count. | Notes. The saved frame actually misses 10 groups and has one extra vessel report. |
| 39,435 | Initial 10 km root cells in the search rectangle. | Initial search map. |
| 64,875 | Evaluated regions across all scales. | Search workload. These are regions, not all point evaluations. |
| 58,118; 6,360; 397 | Rejected regions, expanded regions and terminal centers. | Current traversal accounting. |
| 148; 67; 182 | Consistent, inconsistent and unresolved terminal centers. | Result map. |
| 2.060304 m | Selected estimate’s WGS84 distance from simulated truth. | Result headline. |
| 12.134402 m | Maximum unresolved-center distance from the selected estimate. | Candidate detail. This is not an error bound around truth. |
| 7.388215 s | Cached input/preparation stage for the current full run. | Timing, separately from index creation. |
| 8.461936 s | Current full search API wall time on GPUs 0–2. | Main timing headline. Not pure GPU-busy time. |
| 23.449189 s | Entire recorded supervisor pipeline. | End-to-end timing. Includes preservation checks, selection and evaluation. |

The global AIS-source count includes 2,248 rows normalized as vessel and 20 without a normalized type. Preserve their source identity in the data slide and explain the classification distinction in notes. Unknown catalog types remain possibilities where a proof cannot rule out a chart object.

## Slide sequence and speaker-note plan

### 1. Bearing-only maritime localization

Visual. A large Charleston map crop containing actual shoreline, canonical chart aids and AIS positions. Use the B17 location as an annotated point. A small coast locator provides geographic context.

On slide. Title and “J.C. Vaught.” No performance numbers yet.

Speaker notes. Introduce the task as identifying the observer’s location from the directions of visible mapped objects. State that this review follows one implemented pipeline from geographic data to a saved search result. Explain that the observation example is simulated on real geography. Do not imply a deployed camera system or a field trial.

Asset. F01. New rendering from saved data. Approximately 30 seconds.

### 2. Where is the observer?

Visual. Matched full Eastern Seaboard and Charleston views, with the observer’s location initially hidden. Reveal the evaluation location after showing the size of the search domain.

On slide. “Eastern Seaboard” and “Charleston.”

Speaker notes. The solver is not given the Charleston position in the full-domain run. The map crop is a presentation device, not a location prior. Explain the distinction between the full search rectangle and the local scene that generated observations. Anchor the audience to the same harbor throughout the talk.

Asset. F01/F02 alternate views. Approximately 1 minute.

### 3. Range uncertainty

Visual. Actual Charleston object coordinates with one observed direction drawn as a ray. Show several explicitly calculated range displacements along that same ray. A second mapped object demonstrates why an erroneous range changes an intersection.

On slide. “Range estimate” and a small displacement value derived from the selected geometry.

Speaker notes. Explain the motivation without citing or comparing prior methods. A range inferred from appearance or another uncertain measurement can move an inferred observer position. The figure is an analytical sensitivity construction, not an experiment and not a failure of every range-based system. Bearing measurements also have uncertainty; the proposed system models that uncertainty explicitly instead of assuming exact angles.

Asset. F03. Mathematical overlay on real mapped objects. Select and calculate the displayed offsets during figure production. Approximately 1 minute.

### 4. Bearings relative to true north

Visual. Actual map with a north arrow, an observer-heading arrow and one mapped-object ray. Add the measured relative angle and resulting global bearing as sequential builds.

On slide. β = wrap(ψ + α). Define symbols with short labels beside their arrows.

Speaker notes. Global azimuth is clockwise from true north. Known heading converts an observer-relative direction into a global bearing. Heading availability is an upstream assumption, not an orientation estimate produced by the current location solver. The current saved measurements already contain global bearings. Any camera-to-heading alignment must be calibrated before using real sensor bearings. Numerical predictions use WGS84 geodesics rather than treating projected grid north as true north.

Asset. F04. Geometry on the actual Charleston scene. Approximately 1.5 minutes.

### 5. Bearings without supplied object identities

Visual. The saved bearing set as a polar display beside the mapped catalog. Initially omit correspondence lines; reveal a few actual final correspondences afterward.

On slide. “64 chart-aid bearings.”

Speaker notes. The challenge includes association. A measured direction does not arrive with the correct catalog object’s identity. The search must identify a position and a compatible assignment. The hard 10 km visibility limit is part of the sensor model; a measured range is not supplied. The forward model still calculates candidate-to-object distances to enforce that limit.

Asset. F05. Render saved measurements and selected assignment. Approximately 1 minute.

### 6. The system

Visual. A flat process diagram built from actual input and output thumbnails. Geographic catalog → simulated bearing frame → spatial search → selected location. Show two observation channels, chart aids and vessels, entering the broader scene model.

On slide. “Data → observations → search → location.”

Speaker notes. Preview the structure of the talk. The current optimized location result uses the aid-bearing channel. AIS objects still appear in the physical scene and angular grouping, but the six vessel bearings are not scored as observed localization constraints. A combined AIS-bearing objective is a proposed evaluation branch that needs implementation verification before results can be shown.

Asset. F06. Code-grounded diagram, not an invented execution trace. Approximately 1 minute.

### 7. Geographic data sources

Visual. Two tightly cropped official website captures, NOAA ENC Direct and the NOAA/Marine Cadastre vessel-traffic page, next to small examples of the downloaded point/polygon data.

On slide. “NOAA ENC” and “Archived AIS.”

Speaker notes. Explain that source websites establish provenance, while the map figures use the archived project data. ENC supplies chart features and geometry; AIS supplies reported vessel positions. Cite the exact source URLs and retrieval/snapshot dates in notes. Current website screenshots must be dated and must not be mistaken for the exact archived service response.

Asset. F07. New documentary screenshots proposed; no new data download needed. Approximately 1 minute.

### 8. The Eastern Seaboard catalog

Visual. A large map with chart locations and AIS records as distinguishable shapes. Use a small symbol key and a scale bar.

On slide. “32,168 chart locations” and “2,268 AIS records.”

Speaker notes. Explain the declared geographic bounds from Florida Keys to Maine and adjacent waters. Describe these as recorded positions, not confirmed simultaneous visible targets. Avoid covering the coast with oversized overlapping symbols. Use honest display aggregation at wide scale and exact points in local views; document any aggregation in notes.

Asset. F08. New rendering from saved geographic archives. Approximately 1 minute.

### 9. Shoreline detail and coverage

Visual. Coast-wide known/unknown coverage beside Charleston shoreline detail, with actual chart-scale coverage footprints or a restrained scale-precedence strip.

On slide. “76.56% known coverage” and “23.44% unknown.”

Speaker notes. These fractions describe the declared geographic bounding box in the source metadata, not the fraction of ocean suitable for navigation. The mosaic selects berthing, harbor, approach, coastal, general and overview data in that precedence. This is variable-scale vector mapping, not a uniform raster meter resolution. Unknown coverage remains unknown and is never colored as verified water. Avoid saying all source conflicts were reconciled; the metadata does not establish that.

Asset. F09. Saved map geometry and archived coverage footprints. Approximately 1.5 minutes.

### 10. Charleston’s mapped objects

Visual. A generous harbor crop with actual aids and AIS positions. Add an inset of a busy channel or shoreline corner.

On slide. Local counts only after an explicitly defined crop is counted.

Speaker notes. Show the audience the objects that produce bearing structure. Explain why waterways, channels and harbor layouts form a recognizable spatial signature. Distinguish the number of records within a map crop, the number inside 10 km, the visible number and the detected number. Do not substitute a global catalog count or a historical crop count for the selected current view.

Asset. F10. New saved-data rendering. Approximately 1 minute.

### 11. Three components, one location

Visual. A tight actual map crop centered at 32.7731847, −79.9054558. Use leader lines to the co-located beacon, light and daymark source records. Reveal their shared canonical location.

On slide. “3 source records → 1 chart object.” A small global annotation can show “64,400 → 32,168.”

Speaker notes. The selected example is not a toy. It contains daymark object 191488, light object 206646 and beacon source object 93433 in chart US5CHSDD.000. Their geographic coordinates are exactly equal. Canonicalization preserves their complete source records and attributes. This same canonical object appears in the final 64-object assignment, connecting data preparation to localization. Nearby but distinct coordinates are not merged by a distance tolerance.

Asset. F11. New rendering of three actual retained source components. Approximately 1.5 minutes.

### 12. The AIS snapshot

Visual. A one-minute time window and the actual selected AIS positions. A small table-like graphic may show multiple source timestamps for one retained record only if those original reports are available.

On slide. “15 July 2023” and “12:00–12:01 UTC.”

Speaker notes. One report nearest the window midpoint was selected per vessel identity. The catalog is historical, not live. The ENC point retrieval and shoreline extraction occurred in October 2026, so this is a mixed-epoch geographic test world. Do not draw measured vessel tracks or velocities from this one retained-position snapshot. Vessel names and MMSIs are not exposed in the distributed point catalog.

Asset. F12. Source-selection diagram and existing position snapshot. Approximately 1 minute.

### 13. Configured observation locations

Visual. The 24 B locations over the coast, with a few real harbor crops arranged geographically.

On slide. “24 configured viewpoints.”

Speaker notes. Present these as locations available to define scenarios, not a 24-location accuracy study. Use the current observer CSV and its water-status/map-hash provenance. Existing visibility-range pages need to be regenerated with current canonicalization and visibility code before becoming current-method evidence. Charleston remains the completed search example.

Asset. F13. New rendering; current-code multiport simulation recordings proposed. Approximately 1 minute.

### 14. From mapped world to observations

Visual. The same Charleston scene in four successive views. Catalog objects, in-range objects, clear visible objects and final bearing reports.

On slide. “Mapped world → visible objects → reports.”

Speaker notes. Introduce the simulator as a controlled forward model. The underlying object locations and land polygons come from real data. Observer placement, detection noise, missed detections and extra vessel detections are simulated. Explain why this permits a known evaluation position while keeping that position out of the search inputs.

Asset. F14. Render saved canonical-frame diagnostics. Approximately 1.5 minutes.

### 15. Sensor configuration

Visual. A single range-and-angle map with concise numerical labels placed on relevant geometry.

On slide. “360°”, “10 km”, “0.1°”, “±0.5°”.

Speaker notes. The angular resolution is a grouping parameter, not the bearing-error standard deviation. The underlying Gaussian has standard deviation 1/6 degree and is sampled subject to a hard half-degree bound. Detection probability is 0.9. Type mislabeling and bearing outliers are disabled. Extra reports are vessels only, with expected Poisson count one. There is no penalty for an unseen chart aid in the deterministic positive-evidence search.

Asset. F15. Current protocol and real map. Approximately 1.5 minutes.

### 16. Visibility range changes the scene

Visual. Matched Charleston maps at 1, 3, 5 and 10 km, sharing the same coordinate frame, scale treatment and object legend.

On slide. Only the four range values and source-derived object counts.

Speaker notes. Separate the range limit from land visibility. Objects within the radius may still be blocked. The saved localization run uses 10 km; the other ranges illustrate the simulator’s configuration rather than additional localization results. New current-code exports should record the in-range and clear-visible counts at each radius. No accuracy values may be inferred from these scene panels.

Asset. F16. Proposed new simulator/visibility recording on existing geography. Approximately 1.5 minutes.

### 17. Land blocks sight lines

Visual. Two genuine saved Charleston rays. One to clear Beacon US5CHSDD object 93428; one to blocked Coastal Light US3SC1CB object 726545. Highlight the actual polygon intersection on the blocked ray.

On slide. “Clear” and “Blocked.” Optional labels “2.75 km” and “5.94 km.”

Speaker notes. The saved diagnostic records the clear target at 2,749.54 m and the blocked target at 5,935.32 m. Its recorded land intersection is 1,816.58 m. These are map-derived calculations, not measured sensor rays. Explain that the simulator and final predictor perform polygon visibility checks. The current raw pruning graph uses a more conservative exclusion model; do not imply this full ray test already applies to every coarse cell.

Asset. F17. Render saved ray records and polygons; no new diagnostic needed. Approximately 1.5 minutes.

### 18. Angular merging

Visual. The actual saved pair that merged into saved observation index 58 (zero-based). Show its two geographic positions, their bearing separation, and the selected nearest representative. Use an enlarged angular inset without moving the actual points.

On slide. “80 visible objects → 79 groups.”

Speaker notes. Angularly unresolved objects can produce one report even when their coordinates are distinct. This sensor grouping is separate from catalog co-location canonicalization. The current implementation uses circular angular grouping and the nearest object’s representative bearing. The recorded pair’s keys and representative are retained in the figure manifest, with full identifiers in notes rather than on the slide.

Asset. F18. Saved group diagnostic and canonical coordinates. Approximately 1.5 minutes.

### 19. Bounded angular noise

Visual. A histogram of the actual saved frame’s recovered bearing errors, if the saved representative correspondence supports exact recovery, beside a selected bearing wedge on the map. Alternatively show only the configured probability density until that recovery is completed.

On slide. “σ = 1/6°” and “|error| ≤ 0.5°.”

Speaker notes. Distinguish a measured histogram of simulated errors from a theoretical configured density. Do not label a probability curve as observed data. Do not use the older 164-report frame’s empirical maximum. For geometric intuition, a half-degree angular deviation at 5 km corresponds to approximately 43.6 m lateral displacement; that is a calculation, not the final localization error. Multiple distinct bearings constrain the position jointly.

Asset. F19. Derived saved-data render or explicitly mathematical density. Approximately 1.5 minutes.

### 20. Missed objects and extra vessel reports

Visual. A source-backed funnel or aligned scene snapshots with the exact current-frame counts.

On slide. “200 → 80 → 79 → 69 + 1 = 70.” Labels identify each stage.

Speaker notes. Two hundred physical objects are within range, 80 are clear-visible, these form 79 groups, 69 groups are detected, and one extra vessel report is added. Ten groups are missed. Do not call every non-visible object land-blocked without inspecting the saved visibility labels. The final set contains 64 aid reports, five detected vessel reports and one extra vessel report. A clutter report has a bearing; it does not have a real mapped vessel position that can be plotted as truth.

Asset. F20. Saved canonical-frame counts, with an additional recording only if needed for individual-object highlighting. Approximately 1.5 minutes.

### 21. Worked harbor examples

Visual. A shared-scale strip of Charleston, Norfolk and Portland, with real shoreline, aids and AIS. Reveal simulated bearing reports only for examples recorded with the current simulator.

On slide. Port labels, view radius and actual report counts.

Speaker notes. These examples explain how geometry differs across waterways. They are not evidence that coast-wide accuracy has already been evaluated from all three ports. New simulated frames and saved diagnostic exports are proposed for the other ports. Preserve their seeds, sensor settings and exact input versions so examples can later become a controlled evaluation set.

Asset. F21. Charleston saved data plus proposed recordings for additional ports. Approximately 2 minutes.

### 22. What the search must establish

Visual. One mathematical formulation with a real-map thumbnail. Observed bearings are matched injectively to possible chart objects at a candidate position.

On slide. β_j(x) = azi_WGS84(x,q_j). A_ij(C) indicates possible support over cell C. Require an injective assignment π with A_i,π(i)(C) = 1 for every positive report i.

Speaker notes. Define observer position x, catalog position q_j, observation θ_i, hard error bound ε_i and candidate cell C. Circular bearing residuals use the wrapped difference. At a point, a compatible object must satisfy the physical bearing bound and relevant visibility/range conditions. For a cell, the graph must overapproximate every explanation anywhere in the cell. A failure of that overgraph proves impossibility; success alone does not prove a valid position.

Asset. F22. Mathematics and actual map context. Approximately 2 minutes.

### 23. An exhaustive search budget

Visual. The actual projected search rectangle with its dimensions and a calculated grid-count expression. Avoid a fabricated runtime bar.

On slide. “1 m grid” and “3.93 × 10^12 positions.”

Speaker notes. The rectangle is 1,644,676.176 m by 2,388,459.103 m. Using ceilings gives 1,644,677 × 2,388,460 = 3,928,245,227,420 grid cells. Even an optimistic full evaluation of 64 report checks per position would require approximately 2.51 × 10^14 checks before accounting for association and visibility. These are back-of-the-envelope counts, not a run. The declared rectangle includes land and offshore regions. Do not convert this count into days or GPU speed without a clearly labeled assumed throughput.

Asset. F23. Derived calculation on the actual bounds. Approximately 1.5 minutes.

### 24. Coarse-to-fine search

Visual. One actual geographic cell subdivided into four children, with the bearing-support envelope narrowing over successive scales. Use one fixed real catalog object.

On slide. “10,000 m → 0.61035 m” and “14 halvings.”

Speaker notes. All surviving regions are subdivided; there is no top-N location cutoff or random start. Coarse-to-fine is useful because a sound coarse-cell rejection removes all its descendants. Without pruning, subdivision alone does not reduce work. Refining all 39,435 roots under the actual stopping rule would yield 10,585,752,207,360 terminal cells and 14,114,336,263,335 total tree nodes. Show that larger calculation in backup, not as a measured runtime.

Asset. F24. Mathematical subdivision of a real cell, clearly distinguished from a recorded survivor trajectory. Approximately 2 minutes.

### 25. Positive evidence rejects impossible cells

Visual. Actual recorded cells demonstrating known land, a report with no possible object, and insufficient distinct possible objects. Use modest captions and actual candidate bounds.

On slide. “Land”, “No explanation”, “Too few markers.”

Speaker notes. A detection asserts that something exists. The code can reject when a positive aid report has no possible chart explanation or the necessary distinct-object support is insufficient. Not observing a mapped aid does not prove absence and creates no chart-aid penalty. Unknown shoreline cannot create a missing chart object, so complete shoreline coverage throughout an arbitrary surrounding square is not required for a chart-catalog impossibility proof. Examples must come from a newly recorded current traversal if their old per-cell states were not retained.

Asset. F25. Proposed current-run decision recording. Approximately 2 minutes.

### 26. Distinct markers must explain distinct reports

Visual. A map crop and its actual recorded report-to-object incidence graph. Highlight a deficient subset and then a retained injective explanation from another recorded cell.

On slide. “Distinct reports → distinct objects.” Optional |N(S)| < |S| on the rejected example.

Speaker notes. Individual support and total nearby-object count are insufficient. Several reports may all rely on the same smaller set of objects. Exact augmenting-path matching tests whether all positive reports can be assigned simultaneously. The visual should use a real current-run graph, recorded in a future diagnostic, rather than a made-up three-node example. Unknown types and visibility remain possible edges; overflow or unsupported bounds must retain a cell.

Asset. F26. Proposed incidence-graph capture during the current-code recording. Approximately 2 minutes.

### 27. Safe rejection and unresolved positions

Visual. Actual known/unknown map geometry and the current terminal candidates, with a compact distinction between supported, disproved and unresolved.

On slide. “Supported”, “Disproved”, “Unresolved.”

Speaker notes. An unrelated nominal numerical warning cannot invalidate an independent physical impossibility proof. Conversely, a nominal grouped-match failure does not by itself establish impossibility when the conservative graph permits an explanation. The current raw graph does not remove edges using full polygon sight lines, and the cached rectangular barrier list is empty. Full polygon LOS remains in simulator and final prediction. State this separation clearly rather than implying the raw graph already proves all land occlusion.

Asset. F27. Current saved candidates plus code-grounded proof diagram. Approximately 1.5 minutes.

### 28. Index reuse and multiple GPUs

Visual. Archived map/catalog → saved spatial structures → three GPU lanes → terminal scoring. Use actual device root totals or measured profile spans as restrained annotations.

On slide. “GPUs 0–2” and “Saved index reused.”

Speaker notes. Geometry/catalog preparation is separate from per-observation search. The existing cache is reused and rebuilding was forbidden in the recorded run. Runtime uploads, input validation and cached-file loading still take time. CPU coordination dispatches root tasks and writes outputs; numerical graph construction, matching and prediction run on the devices. Completed root totals were 13,899, 13,552 and 11,984 for GPUs 0, 1 and 2. Do not call this zero CPU usage or imply a persistent GPU process was already measured across multiple observation frames.

Asset. F28. Code diagram and saved per-device trace. Approximately 1.5 minutes.

### 29. The coast-wide search contracts toward Charleston

Visual. The principal two-panel animation. Left shows the fixed full coast. Right shows Charleston Harbor with a locator rectangle. Advance both panels through the same recorded decisions/scales, then finish at a meter-scale local inset.

On slide. Current cell width, retained count and rejected count. Use a small “Scale-ordered trace” caption if presentation playback is reordered by scale.

Speaker notes. A parallel search does not execute globally synchronized spatial passes. Either show actual recorded event order or explain that events are grouped by dyadic scale for understanding. The right-hand crop is for presentation only. Keep distant survivors visible on the left until they are actually rejected. Never derive early survivor masks from only the ancestors of final candidates. The prior animation cannot be substituted because it records an older algorithm.

Asset. F29. New approved recording required. Target 30–45 seconds of presentation playback; do not label its duration the measured search runtime. Approximately 2 minutes including explanation.

### 30. Recorded search workload

Visual. Current-run count accounting and a spatial snapshot. A simple flow accounts for every evaluated region.

On slide. “64,875 evaluated”, “58,118 rejected”, “6,360 expanded”, “397 terminal.”

Speaker notes. The exact identities are 58,118 + 6,360 + 397 = 64,875 and 39,435 + 4 × 6,360 = 64,875. These provide a clear view of what coarse rejection saves without comparing code versions. Regions at different scales are not equivalent point samples, so do not label this 64,875 one-meter positions. The hypothetical exhaustive tree belongs to the calculation slide, not a measured baseline bar.

Asset. F30. Render existing current counts; no new run required. Approximately 1 minute.

### 31. The selected Charleston position

Visual. Coast overview, harbor inset and meter-scale result inset. Show the selected estimate, simulated truth and actual matched chart-aid rays in controlled builds.

On slide. “2.06 m” and “64 / 64.”

Speaker notes. The 2.060304 m distance is an independent WGS84 evaluation after the selected estimate was frozen. The solver did not receive the true position. The chosen point minimizes summed absolute bearing residual among numerically consistent, fully matched candidates, with fixed coordinate tie-breaking. The summed residual is 6.2809796 degrees, not the average error. This result covers one simulated Charleston frame searched over the full declared coast domain.

Asset. F31. New render of saved terminal archive, frozen estimate and evaluation file. Approximately 1.5 minutes.

### 32. The remaining local alternatives

Visual. A readable meter grid around the result with different shapes/colors for the three saved statuses. Retain a harbor locator inset for context.

On slide. “148 consistent”, “67 inconsistent”, “182 unresolved.”

Speaker notes. Each center is one tested coordinate of a terminal cell. Consistency at a center does not prove every point of its cell is feasible. All unresolved centers lie within 12.1344 m of the selected estimate. They explain 60–63 reports in nominal grouped scoring, while the conservative physical graph permits possible explanations. No distant coastal candidates remain. The unresolved spread is not a statistical confidence interval or a guaranteed bound around truth.

Asset. F32. Current terminal archive; no new run required. Approximately 1.5 minutes.

### 33. Chart aids versus chart aids plus AIS

Visual. A matched pair of the same scene and observations. One panel highlights the aid channel; the other highlights the proposed additional vessel-bearing constraints. Show numerical result plots only after the actual comparison exists.

On slide. “Chart aids” and “Chart aids + AIS.” Until evaluated, a small label reads “Proposed comparison.”

Speaker notes. Hold the physical scene, map, detection frame and heading fixed. The comparison should vary which observed-bearing channels enter the objective, not silently remove AIS from the simulated world and thereby change angular grouping. Aid consistency stays deterministic and positive-only. Vessel observations need an explicit treatment of unmatched vessels, non-AIS boats, stale positions and vessel-only clutter. Verify or implement a compatible combined objective before running a claimed ablation. Current 2.06 m and 8.46 s values belong only to the aid-bearing objective.

Asset. F33. Planned implementation verification and paired experiments. No comparative performance numbers presently supported. Approximately 1.5 minutes.

### 34. Time to locate the observer

Visual. Three large measured duration bars with clearly separated timing scopes. Keep hardware and observation count in small supporting labels.

On slide. “7.39 s cached input”, “8.46 s search”, “23.45 s complete pipeline.”

Speaker notes. The 8.46 s search wall time includes device setup and coordination/output inside the API, rather than representing CUDA kernel busy time alone. The full pipeline also includes preservation checks, saved-output selection and separate evaluation. Include the recorded device models and memory capacities in notes if available; otherwise capture a dated hardware inventory separately, without running a benchmark. These three bars are different timing scopes and should not be stacked as if they were three non-overlapping phases. Use a precise timeline only after phase boundaries are actually recorded. Do not add a 98.5 s old-code bar, an 11.6× label, or an unmeasured brute-force runtime.

Asset. F34. Existing current-run durations and hardware records. Approximately 1 minute.

### 35. What is prepared once and what repeats

Visual. The actual preparation/cache workflow, with recorded cache hits and a repeat-observation arrow whose unmeasured portions are labeled in notes.

On slide. “Map/index cache reused.” Optional “1.31 GB archive.”

Speaker notes. Separate geographic index construction from reading the saved archive, projecting/validating fixed inputs, device uploads, per-frame observations and search. The base archive is 1,306,233,869 bytes on disk; this is not a GPU-memory measurement. The object sidecar is 7,073,914 bytes. Initial construction time and warm persistent multi-frame throughput are not established by the current 7.39 s cached loading measurement. Proposed future recordings can expose those stages without recreating the existing cache unnecessarily.

Asset. F35. Source-defined workflow plus existing fingerprints. Approximately 1 minute.

### 36. Current capability and next evaluation

Visual. The final Charleston map next to a small map strip of planned additional ports. Keep the measured result visually dominant and the planned work clearly marked.

On slide. “64 bearings · 2.06 m · 8.46 s.” A restrained second line may read “Mixed observations and additional ports.”

Speaker notes. Close with the implemented capability, its actual data basis and the observed result. Explain the remaining work as a concrete evaluation program. Record the current traversal for presentation, resolve local visibility/grouping proofs as needed, evaluate the combined vessel-bearing objective and then use a declared multiport test set. Do not claim these future experiments have already succeeded.

Asset. Reuse F31 as a small background/context crop only if needed; otherwise use a different view of the same recorded result. Approximately 1 minute.

## Figure production and recording specification

The following categories belong in the production checklist. “Render” means drawing from saved records, not running another search. “Record” means a proposed future execution with a declared scope.

| Figure group | Evidence status | Required work |
|---|---|---|
| F01, F02, F08–F13 | Actual geographic archives exist. | Render current map/catalog crops and source-count graphics. Capture official websites later. |
| F03, F04, F22–F24 | Mathematical constructions on actual coordinates. | Compute declared geometry/calculations and render with Typst. Clearly label the calculation figures. |
| F05, F14, F15, F17–F20 | Exact current frame and supporting diagnostics exist. | Export the saved representative/report links; render without substituting historical frames. Verify which error residuals can be recovered from saved truth. |
| F16, F21 | Additional configured scenarios, not completed localization experiments. | Propose current-code visibility/simulator recordings at the requested radii and ports. |
| F25, F26, F29 | Current full intermediate spatial decisions/graphs were not preserved. | Record a current-code traversal or a matched pruning replay with exact decision geometry and representative incidence graphs. |
| F27, F30–F32, F34, F35 | Current final outputs, counts and profiles exist. | Render the saved result and timing scopes. |
| F33 | No current combined observed-AIS-bearing accuracy/timing result. | First verify or implement the combined objective, then design and authorize paired measurements. |

The dual-view animation should be one synchronized 16:9 MP4, with an accompanying static storyboard and a PDF fallback. Use a fixed coast panel and a Charleston panel with linked locator bounds. Show 10 km cells through the actual dyadic scales down to 0.6103515625 m, selecting roughly 10–15 key frames to keep the movie readable. Final local views can show 10–20 m of water with a harbor inset, rather than discarding the geographic reference.

Record each evaluated rectangle once with its exact bounds, parent/depth, root-task range, GPU identity, per-device batch identifier, decision and uncertainty/capacity flags. Record monotonic dispatch/completion times if actual concurrency is to be shown. Save rejected cells as well as retained cells, rather than reconstructing early stages from final survivors. Capture one real incidence graph for each explanatory rejection category needed by the slides.

Preserve current code revision, map/catalog hashes, cache IDs, measurement frame, hard tolerances and bounds. A pruning-only replay must reproduce the current 64,875 evaluations, 58,118 rejections and 397 terminal rectangles before its geometry is presented as the current run. Reuse the original frozen terminal scoring for overlays. Replay wall time is not the recorded 8.46 s runtime. A newly instrumented full search has its own measured duration and needs a distinct run identifier.

At every scale, the full-coast panel must retain all surviving regions. A Charleston-only view must not hide distant surviving alternatives. If small cells are enlarged to a display pixel, disclose that display aggregation. Use the same epoch and decision state for both panels. Honor presentation playback controls and supply a poster image so a missing video codec cannot interrupt the meeting.

Do not reuse the existing pruning playback as current evidence. Its provenance is the older 1,337,411-region traversal. Similarly, old B-point visibility illustrations require current-code provenance checks, and the older 164-report noise summary cannot supply counts or error maxima for the current 70-report frame.

## Proposed AIS comparison protocol

Use identical saved geographic input versions, heading assumptions and base physical scenes for both conditions. Start with the same full observation frame. Condition A supplies chart-aid bearings to the existing deterministic objective. Condition B additionally supplies the observed vessel-bearing channel to a verified combined objective, while preserving the aid constraints.

Do not obtain Condition A by deleting AIS objects before simulation, because that can change the scene’s angular merging and representative selection. Do not mark unobserved chart aids as negative evidence. The vessel model may handle non-AIS detections, vessel-only clutter and stale or offset AIS positions explicitly; these assumptions belong in the notes and execution protocol.

For future experiments, predeclare the ports, observer locations, radii, seeds, bearing bounds, missing-detection settings, AIS timing and object identity rules. Use exactly the same frame in each paired comparison. Report WGS84 error, supported/unresolved candidate spread, correct associations when truth exists, completed coverage and timed stages. Include failures rather than selecting only successful harbor scenes. Do not populate a result figure until actual measurements exist.

## Backup slides

| Backup | Visual and discussion |
|---|---|
| A1. Bearing and coordinate conventions. | True north, grid north, known heading, circular wrap and WGS84 azimuth on one actual location. |
| A2. Complete sensor specification. | The actual settings file, a compact parameter table and the exact 70-report generation counts. |
| A3. Whole-cell uncertainty envelope. | Derivation of the conservative object bearing/range envelope and unsupported-bound retention. |
| A4. Injective feasibility proof. | The recorded real incidence graph, Hall condition, augmenting path and identity-certificate requirements. |
| A5. Exhaustive count arithmetic. | Exact rectangular 1 m count, 14-level unpruned tree count and assumptions. Optional memory arithmetic labeled as calculated, not measured. |
| A6. Catalog provenance and epochs. | Raw/canonical counts, component attributes, archived AIS window and chart source/retrieval dates. |
| A7. Timing definitions. | Scope boundaries, sampled CUDA event spans versus wall time, CPU coordination and saved cache hits. |
| A8. Remaining unresolved centers. | Detailed actual local examples, empty rectangle-barrier list, and the difference between nominal support and an independent rejection proof. |

## Speaker notes and slide composition

Every slide should have one visual question to answer. Aim for a title and at most two large numerical callouts on normal visual slides. Source captions and necessary legends may be small but readable. The mathematical slides may use more notation, but their prose explanation still belongs in notes. Avoid screenshots of code, terminal logs, JSON dumps or large tables as substitutes for explanation.

Use notes in a consistent order. State the takeaway, walk through the exact elements of the figure, explain assumptions and definitions needed to interpret it, then give a transition to the next slide. Put source URLs, file paths, snapshot dates, run IDs and derivations at the end of the notes. The notes should support delivery by someone who did not write the code.

Maintain separate visual encodings for data source, object type and candidate status. An AIS symbol should not reuse the same color/shape that signifies an inconsistent candidate. Keep north orientation, grid units and coastline tone consistent. At meter scale, retain a locator inset and mark evaluation truth separately from the selected estimate. Never use color alone to distinguish accepted/rejected/unresolved states.

A figure manifest should list figure ID, target slide, input paths/hashes, code revision, observation/run identifier, coordinate reference system, crop bounds, symbol scaling, recording/render status and speaker-note source. This is how the final PowerPoint can remain nearly text-free without losing provenance.

## Source registry

Repository baseline inspected for this plan is commit d223427 on branch gpu-ransac-pair-search. The authoritative checkout is /home/jvaught2/projects/bearing-only-maritime-localization on comech-2080.

| Evidence | Repository or saved-run source |
|---|---|
| Source metadata and epochs. | data/sources.json. |
| Archived real geometry. | data/map.json.gz; source map version noaa-enc-east-coast-fullscale-map-v4:2026-10-26T19:56:03Z. |
| Real catalog positions. | data/objects-ais.json.gz; data/objects-chart-aids.json.gz; data/objects-chart-aids-canonical.json.gz. |
| Canonical counts and provenance preservation. | docs/verification/chart-aid-canonical-catalog.json; source_components inside canonical catalog. |
| Configured sites. | data/observer_locations.csv. |
| Exact map used by the recorded search and LOS calculations. | runs/charleston-search/20261026T222955253680Z/projected-valid-map.json, as recorded in the current result settings. |
| Exact current simulated observation frame. | docs/verification/charleston-blind-measurements.json. |
| Exact sensor protocol. | runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json. |
| Canonical frame, actual LOS and merged pair. | runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json. |
| Simulator implementation. | maritime/simulator/runner.py; mismatch.py; maritime/geometry/angular_grouping.py. |
| Current solver input selection. | tools/run_resident_marker_search.py. |
| Region traversal and proof implementation. | maritime/search/gpu/resident/engine.py; chart_pruning.py; distinct_pruning.py; distinct_pruning.cu. |
| Current outcome and execution protocol. | docs/verification/charleston-coastwide-distinct-result.json. |
| All current terminal points. | docs/verification/charleston-coastwide-distinct-terminal-results.npz. |
| Current timing and device records. | docs/verification/charleston-coastwide-distinct-profiles.zip. |

Official source pages for later documentary captures are [NOAA ENC Direct](https://www.nauticalcharts.noaa.gov/learn/encdirect/), [NOAA Vessel Traffic](https://coast.noaa.gov/digitalcoast/data/vesseltraffic.html), and the [NOAA harbor GIS service](https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_harbour/MapServer). These source pages were checked for planning; the proposed screenshots have not been captured. Website service content is not a substitute for the archived snapshot used by the recorded experiment.

## Proposed final deliverables

| Deliverable | Contents |
|---|---|
| PowerPoint. | Approximately 36 visual main slides and 8 backup slides, editable titles/numbers, full speaker notes and embedded synchronized MP4. |
| Static slide PDF. | All slides with meaningful video poster frames or storyboards. |
| Meeting notes. | A separate Markdown or PDF companion with slide thumbnails, narration, derivations, parameter definitions, provenance and transitions. |
| Figure library. | Typst sources, imported source-data files, rendered figures and exact input/version manifests. |
| Recording package. | Current spatial decision trace, selected real incidence graphs, movie source frames and a documented relationship to the measured result. |

## Recommended production order

First, approve the storyline and the interpretation of real geographic data with explicitly simulated observations. Then build the reusable coast and Charleston basemaps, source-count graphics, real canonical-object example and current-frame simulator panels. These establish visual language without any new localization experiments.

Next, record the current algorithm with the exact spatial decisions and explanatory incidence graphs needed for the synchronized movie. Produce the mathematical search slides alongside that recording. Render current final candidates and timing figures directly from the already saved current results.

Finally, verify the implementation scope of the combined AIS-bearing objective and obtain a separate paired evaluation before adding comparative result bars. Assemble the PowerPoint with full speaker notes, embedded media and static fallbacks, then inspect every rendered slide and test media playback. Planning does not require running any of those experiments now.
