# Bearing-only maritime localization. Meeting notes

Written by J.C. Vaught.

This companion follows the 33 main slides and eight backups. The deck has page totals and six numbered sections. The original saved experiment supplies accuracy and timing. The approved pruning-only replay supplies the current spatial trace and incidence witness.

## 1. Bearing-only maritime localization

We want to locate a boat from the directions of objects around it. The geographic map supplies possible object locations, but the bearing reports do not identify which catalog entry produced each observation. That makes the problem both geometric and combinatorial. Charleston will provide one recurring scene as we move from the full coast into the harbor and then to individual candidates. The map combines electronic navigational chart features with archived Automatic Identification System positions. Those are real geographic inputs. The observations and observer used for the localization result are simulated. This is a review of the implemented method and one saved experiment, rather than a report of an onboard field deployment. Keeping the same harbor throughout the talk lets us connect every later numerical result to a specific piece of geography.

Transition. Begin with the size of the location question.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 2. Where is the observer?

The left view is the full declared Eastern Seaboard search rectangle. The Charleston inset lets us understand the observation scene, but that crop was not supplied as the position prior in the coast-wide experiment. Initially the observer is hidden so we can distinguish what the search receives from what the evaluator knows. The evaluator later uses the chosen Charleston coordinate to measure error. The solver instead receives the coast-wide catalog, map, bearing frame and search bounds. It must preserve alternatives elsewhere until the positive evidence rules them out. This distinction matters whenever a presentation zooms toward a result. A display crop can improve readability, but it cannot silently reduce the computational search area. The next example motivates why directions can be useful even when object range is uncertain.

Transition. Use the actual harbor to motivate uncertain range.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 3. Range uncertainty

Consider the saved Charleston observer and a mapped object along one bearing. Changing an inferred range moves the proposed observer along that same ray. The inset magnifies a calculated plus-or-minus ten-percent range change, corresponding to approximately plus-or-minus 276 projected meters on this geometry. The object positions and ray remain fixed; the displaced observer marks show the range sensitivity. This is a mathematical construction on real mapped coordinates. The search uses bearing constraints and a declared visibility range without accepting measured object ranges as input. Multiple directions then constrain the location jointly. Before combining those directions, we need to state their reference orientation.

Transition. Define the orientation convention before using bearings.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json.

## 4. Bearings relative to true north

A relative camera direction and a globally referenced bearing are different quantities. Let $\alpha$ be the observer-relative direction and $\psi$ the known heading clockwise from true north. Their sum, wrapped around the circle, gives the global bearing $\beta$. The figure uses the saved Charleston observer and mapped clear Beacon 93428. Its WGS84 azimuth is $\beta = 86.6177095761^\circ$. For this declared calculation, choose $\psi = 30^\circ$; the corresponding relative direction is $\alpha = 56.6177095761^\circ$. The thirty-degree heading is an illustrative input, not a recorded vessel heading. The current measurement file already contains global bearings, so the location solver does not estimate heading as another search dimension. Heading and sensor alignment are upstream assumptions. The true-north and heading rays use geodesically derived endpoints. Their projected angles differ from map-axis directions because grid north and true north differ on this projection.

Transition. Separate directions from object identities.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## 5. Bearings without supplied object identities

The saved input contains 64 positive chart-aid bearings. Each report gives a direction, type and uncertainty information, but it does not carry the correct chart-object identity or range into the search. At a proposed observer position, the catalog predicts several possible objects in similar directions. A valid explanation must associate the reports with compatible distinct physical objects. That is why intersecting a few pre-identified rays would not represent the whole implemented problem. The correspondence lines revealed here come from the saved selected assignment, after the search, rather than from identities supplied to it. The ten-kilometer range remains useful because it bounds which objects might be visible from a candidate. It is a sensor-model limit, not a measured range attached to each observation.

Transition. Preview the complete input-to-output path.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 6. The system

This is the complete input-to-output relationship. Geographic objects and shoreline geometry define the world. A forward sensor model produces the observation frame. The reverse search asks which observer positions can explain the positive reports, and a declared selector chooses among certified terminal candidates. Two object channels appear in the scene: chart aids and vessels. The current coast-wide result uses only the chart-aid bearing reports as observed localization constraints. AIS-source objects can still affect the physical scene and its angular grouping, but the six observed vessel bearings are not passed into the current deterministic aid objective. A combined observed-vessel-bearing objective remains a proposed evaluation branch. That distinction will remain visible when we discuss results, so the audience does not attribute the reported accuracy to information that the solver did not use.

Transition. Establish the geographic source provenance.

Sources. maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 7. Geographic data sources

The source pages establish where the geographic inputs came from. NOAA electronic navigational charts supply the chart features and shoreline-related geometry. The archived vessel-traffic data supply reported vessel positions. The actual analysis uses saved project archives, rather than fetching changing service responses during the search. The website captures should carry their capture dates, while the notes identify the archive and snapshot dates used by the experiment. This prevents a current screenshot from being mistaken for the original service response. Chart source records also vary in scale and epoch. We retain their metadata and provenance instead of presenting a single universal map resolution or claiming that every source conflict has been reconciled. The next map shows the actual quantity and geographic distribution of those saved objects.

Transition. Show the distribution of the saved catalog.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json.

## 8. The Eastern Seaboard catalog

The coast-wide catalog contains 32,168 canonical exact-coordinate chart locations and 2,268 AIS-source records. Together they form the 34,436-object canonical geographic catalog used by the saved experiment. The chart count includes 31,925 locations classified as physical aids and 243 retaining unknown classification. The AIS-source count includes 2,248 normalized vessel rows and 20 rows without a normalized type. Their source identity remains AIS even when classification is unresolved. At this scale, point symbols may require documented display aggregation to remain legible; the local views use the actual positions. These counts describe catalog entries across the declared domain, not objects simultaneously visible from Charleston. We next zoom into the harbor to connect that distribution with the structure available to a bearing-only search.

Transition. Zoom into the recurring harbor.

Sources. data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json.

## 9. Charleston’s mapped objects

Charleston provides a more useful view of why the geographic signature is informative. Charted features follow channels, shoreline corners and harbor structures, while the archived AIS positions supply a different spatial pattern. A local inset can expose a busy section without changing its coordinates. Several different counts may be relevant here, and they must stay separate. A map crop contains one set of records; the sensor radius selects a subset; land visibility reduces that subset further; grouping and missed detections then determine the final reports. The displayed local count should therefore be computed for the declared crop and labeled accordingly. Before generating observations, we also need to distinguish physical objects from the multiple chart components that can occupy one exact location.

Transition. Resolve chart components before counting physical markers.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json.

## 10. Three components, one location

This particular chart location illustrates the identity preparation directly. The daymark, light and beacon component records have exactly the same latitude and longitude, 32.7731847 and minus 79.9054558. Their source identifiers include daymark 191488, light 206646 and beacon 93433 in chart US5CHSDD.000. The canonicalizer represents that exact coordinate once while retaining the original component records and their attributes. It does not merge nearby coordinates using a distance threshold. This matters because counting every chart component as a distinct visible marker could produce false support for several observations. The resulting canonical object also appears in the final 64-object assignment, so this is part of the actual data path. Across the source catalog, 64,400 component rows become 32,168 exact-coordinate locations.

Transition. Establish the independent AIS time snapshot.

Sources. data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json; docs/chart-aid-identity.md.

## 11. The AIS snapshot

The AIS inputs come from a one-minute archived window on July 15, 2023, from 12:00 to 12:01 UTC. For each vessel identity, the retained report is the one nearest the midpoint of that window. That gives a position snapshot rather than a measured track for this presentation. We should not infer an observed path or velocity history from a single retained position. The distributed point catalog also omits vessel names and MMSIs. The chart extraction occurred in October 2026, so the physical scene is a mixed-epoch geographic test world rather than a reconstruction of one fully synchronized real harbor moment. This limits how we interpret a future AIS-bearing experiment. For the current result, the observed vessel-bearing channel remains outside the localization objective.

Transition. Locate the configured scenarios along the coast.

Sources. data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json.

## 12. Test locations

The project defines 24 B locations along the coast. They provide configured viewpoints for creating scenarios, and their stored map status and map hash record the geographic check used when they were chosen. They are not 24 completed accuracy experiments. Charleston is the completed coast-wide localization case discussed in this review. Additional harbor crops can show how channel geometry and object density vary, but displaying a viewpoint does not establish a successful result there. The current CSV should control the displayed coordinates rather than older visibility illustrations. Any new simulated frame for another port needs its own recorded settings, inputs and diagnostic outputs before it can enter the evaluation set. With the map and viewpoints defined, we can now state exactly what the simulator produces.

Transition. Move from geographic inputs to generated observations.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/observer_locations.csv.

## 13. What the simulator produces

The two linked views show opposite directions through the problem. In the forward direction, the simulator receives the map, catalog, chosen observer position and sensor configuration. It calculates range and land visibility, combines angularly unresolved objects and applies the configured detection effects. Its output is the observation list. In the reverse direction, the search receives that list and the geographic inputs, while the chosen observer position remains separate. It must infer where the boat could be. The known observer coordinate is retained only so the evaluator can later measure the selected estimate's error. The reports shown here are the exact saved 70-report canonical frame, with 64 aid reports and six vessel reports. They are simulated observations on real geographic geometry. The next slides unpack each forward-model operation.

Transition. Specify the sensor that produced the saved frame.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 14. Sensor configuration

The sensor has a full-circle view and a ten-kilometer range limit. Its 0.1-degree angular resolution controls which nearby directions merge into one report. That resolution is separate from bearing noise. The underlying Gaussian noise distribution has standard deviation one-sixth of a degree, and rejection sampling restricts accepted draws to a hard half-degree bound. Detection probability is 0.9, allowing real groups to be missed. Deliberate type errors and the separate bearing-outlier mechanism are both disabled. Additional reports represent vessels only, with a Poisson mean of one. The saved frame contains one such extra report, but that count is a realization rather than a guaranteed count in every frame. The deterministic chart-aid search uses positive detections and applies no penalty when a listed aid is not observed.

Transition. Separate radius from visibility.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 15. Visibility range changes the scene

These panels filter the same 200 saved in-range physical objects and their recorded line-of-sight statuses at four radius limits. At one kilometer, all six objects are visible. At three kilometers, 45 of 54 are visible; at five kilometers, 70 of 102 are visible; at ten kilometers, 80 of 200 are visible. Each label gives visible objects over objects inside the radius. The panels retain the actual Charleston coordinates and a shared object legend. They show how distance and land blocking jointly change available geometric support. They do not introduce separately simulated detection or noise frames at each radius. The saved localization result uses the ten-kilometer frame. The next pair of rays isolates why land blocks one mapped object while another remains visible.

Transition. Show how land changes a line of sight.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 16. Land blocks sight lines

These two examples use actual saved chart coordinates and recorded map-derived visibility. The clear beacon, source object 93428 in US5CHSDD, is 2,749.54 meters from the chosen observer. The blocked beacon, source object 93424 in US5CHSDC, is 7,372.43 meters away. Its saved diagnostic records 2,797.49 meters of raw-land intersection. The two rays should have separate readable panels or bounds that include both actual targets. These are geographic calculations, not measured camera rays. The simulator and final candidate predictor apply the full polygon sight-line calculation. The raw pruning graph uses a deliberately more permissive model and does not yet remove every edge through this complete polygon test. That separation matters later when we interpret unresolved candidates. Visibility reduces the scene, after which angular resolution can reduce the number of reports again.

Transition. Separate visibility from angular resolution.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json.

## 17. Angular merging

The saved canonical scene has 80 clear-visible physical objects but only 79 angular groups. The illustrated pair contains two distinct mapped positions at bearings 343.59362054 and 343.68509401 degrees. Their separation is 0.09147347 degrees, below the configured 0.1-degree grouping threshold. Their ranges are approximately 2.036 and 4.433 kilometers, so the nearer object supplies the representative bearing and type. This pair produced saved observation index 58, using zero-based indexing. The map preserves both actual coordinates, while the angular inset magnifies their directional separation. Circular angular grouping also handles adjacent directions across the zero-degree wrap. This differs from exact-coordinate canonicalization, which resolves multiple chart components at one location. Angular merging instead describes distinct visible objects that the sensor cannot resolve in direction. The representative then receives the bounded sensor error described next.

Transition. Add the declared bearing error to the representative direction.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 18. Bounded angular noise

The histogram contains the recovered bearing errors of the 69 detected physical groups in this exact saved Charleston frame. The additional vessel report is not a noisy observation of a physical target, so it is excluded from this histogram. The largest recovered absolute error is approximately 0.392584 degrees, within the configured half-degree bound. The generating distribution is Gaussian with underlying standard deviation one-sixth of a degree, rejection-sampled inside that bound. The histogram describes simulated draws on real geographic objects; it is not a distribution measured by an onboard sensor. The map wedge gives geometric intuition. At five kilometers, a half-degree deviation corresponds to approximately 43.6 meters of lateral displacement. That is a sensitivity calculation, not the selected localization error. Multiple bearings from distinct objects constrain the observer jointly.

Transition. Account for missed and extra reports.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 19. Missed objects and extra vessel reports

The funnel records the exact current frame. Two hundred canonical physical objects are within range. Eighty have clear map-derived visibility, and those form 79 angular groups. Sixty-nine groups are detected. One additional vessel report brings the total to 70. Ten groups were therefore missed. The final labels are 64 chart-aid reports, five detected-vessel reports and one extra vessel report. The saved range-status export identifies the remaining 120 in-range physical objects as blocked in this frame; that is the recorded label, rather than an inference from their absence. The extra report has a direction and a vessel label, but no actual mapped position; drawing it at an arbitrary distance would manufacture data. Only the aid-bearing subset enters the current deterministic localization objective.

Transition. Explain why harbor geometry motivates multiple scenarios.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 20. Worked harbor examples

Harbor geometry varies, so the same sensor settings need not produce equally informative scenes at every port. A shared-scale view of Charleston, Norfolk and Portland can show real differences in channels, shoreline layout and catalog object density. Those geographic panels can be drawn from existing archives. However, report bearings and report counts should appear only for frames actually recorded with the current simulator. The other port frames and their diagnostic exports remain proposed work. Their appearance in a map strip must not imply completed coast-wide accuracy evaluations from all three locations. Future comparisons should preserve their geographic versions, observer choices, seeds and sensor assumptions so the scenes form a declared evaluation set. We now turn from forward-model examples to the search's required logical statement.

Transition. State the mathematical compatibility requirement.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## 21. What the search must establish

Let the observer position be $x$, a mapped object position be $q_j$ and a positive report be $\theta_i$ with hard tolerance $\epsilon_i$. The predicted global direction is the WGS84 azimuth from $x$ to $q_j$. At a single candidate point, range, visibility and the wrapped bearing residual define possible explanations. The assignment must be injective because distinct trusted marker detections require distinct physical objects. For a whole cell $C$, the incidence graph must include every explanation that could hold anywhere within that cell. This graph deliberately permits extra possibilities when a bound or visibility condition is uncertain. If even that expanded graph cannot support all reports, the entire cell is impossible. A successful graph match is only a necessary condition; it does not itself certify a final position.

Transition. Calculate the cost of checking every fine location.

Sources. maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## 22. Exhaustive search, even across multiple GPUs

The declared projected rectangle is about 1,644,676 meters wide and 2,388,459 meters high. A calculated one-meter grid therefore contains approximately 3.93 trillion positions. Even a simplified budget of 64 report checks per position already implies about 251 trillion checks, before association and visibility are considered. Dividing the positions perfectly among three GPUs still leaves about 1.31 trillion positions for each device. This is arithmetic on the real bounds, not a benchmark. Multiple devices divide work; pruning changes how much work remains. The current implementation uses GPUs zero through two for the numerical search, while CPU coordination launches work and writes outputs. We do not infer days of brute-force runtime from an unmeasured throughput. The next slide shows how whole-region reasoning avoids visiting most of those fine locations.

Transition. Replace exhaustive point enumeration with hierarchical regions.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## 23. Coarse-to-fine search

The search starts with ten-kilometer root cells and subdivides every surviving cell into four children. It stops when a cell's maximum width is at most one meter. Under this dyadic schedule the nominal full-cell width reaches approximately 0.61035 meters after 14 halvings. There is no fixed top-N shortlist or random initial location sample. That preserves coverage, but subdivision alone does not reduce work. An unpruned tree would be enormous. The advantage comes from rejecting a coarse cell with a sound proof, which removes all of its descendants at once. The illustrated cell is an actual geographic region, while the subdivision overlay is mathematical unless it is tied to a recorded traversal. We next separate the predicates that can establish such a rejection from conditions that only leave a location unresolved.

Transition. Identify which evidence can reject a region.

Sources. tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 24. Pruning

A positive chart-aid detection asserts an existing object. The four conditions shown can reject an entire candidate cell. The cell may be entirely known land, a report may have no possible object, there may be too few distinct possibilities, or no simultaneous injective assignment may exist. Missing a mapped aid never creates a penalty. The approved pruning-only recording captured every decision using the current GPU predicate. It contains 5,931 land rejections,39,011 individual-support rejections,475 distinct-count rejections and 12,701 injective-matching rejections. Their sum is 58,118. Unknown geometry cannot remove a possible explanation without a valid proof. The next slide shows an actual recorded matching rejection rather than a constructed adjacency example.

Transition. Move from individual support to simultaneous distinct support.

Sources. maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 25. Distinct markers must explain distinct reports

This graph is extracted from a real rejected cell in the approved coast-wide pruning recording. Reports 38,39 and 40 use zero-based order within the 64 positive aid reports. Every one of these three reports can be explained individually. However, their complete combined neighborhood contains only two canonical catalog entries. All six possible edges for this subset are drawn. Three distinct reports cannot be assigned to only two objects, so the entire candidate cell is rejected. The displayed object labels $q_0$ and $q_1$ are local drawing indices. They refer to snapshot columns 867 and 868, catalog rows 14232 and 14231; their complete canonical keys and supplied positions are preserved in the witness JSON. The full cell graph contains 1,359 candidate objects. The failure comes from the subset relationship, rather than a shortage in that overall count. Both graph flags and proof validity were checked in the recorded GPU output. The CPU extracted the explanatory subset afterward from saved adjacency; it did not decide search pruning. This is a recorded example of the Hall condition. The next slide distinguishes this conservative cell proof from final point scoring.

Transition. Distinguish impossibility from retained uncertainty.

Sources. maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md; docs/presentation-draft/pruning/witness.json; docs/presentation-draft/pruning/pruning-graphs/gpu-0-state-4.npz.

## 26. Deterministic marker scoring

After conservative pruning, deterministic point scoring asks whether a center has a complete explanation for all 64 positive marker reports. The assignment must be injective, so distinct reports use distinct physical markers. Every selected wrapped bearing residual must satisfy the hard half-degree tolerance, including the numerical margin required by the implementation. A certified consistent center also requires zero numerical flags, known water at the observer, and certified visibility and grouping for the matched support. Unmatched catalog objects are not penalized because missing detections are allowed. The selector considers consistent centers with complete assignments and minimizes the sum of wrapped absolute bearing residuals. The selected Charleston center has a sum of 6.2809796 degrees across 64 matches; this is not a mean. Fixed east-then-north tie-breaking makes selection deterministic. The estimate is saved and hashed before the separate evaluator reads simulated truth. The conservative proof behavior and retained unresolved centers remain distinct from this ranking step.

Transition. Follow the full search from the coast into the surviving harbor region.

Sources. maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py; tools/run_resident_marker_search.py; docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 27. The coast-wide search contracts toward Charleston

The video shows the approved recording of every current pruning decision. It begins with all 39,435 root cells. The first 10 km pruning retains 851 and rejects 38,584. Subsequent frames group actual decisions by generation through 14 halvings; the ending cell width is 0.6103515625 m. Retained cell counts can increase when one region is replaced by four children. That count increase does not mean a larger candidate area. The fixed left panel keeps all surviving coastal regions visible. The right panel begins as a Charleston reference crop and zooms to all remaining regions once their global bounds fit in that harbor view. Earlier masks come from the recorded decisions, never from ancestors reconstructed only from final survivors. The trace contains 64,875 evaluated regions,58,118 rejections and 6,360 expansions, leaving 397 terminal rectangles. All recorded roots and four-child expansions were checked, and the final 397 rectangles are bitwise identical to the saved timed run. The final classification overlay comes from that frozen run and shows 148 consistent,67 inconsistent and 182 unresolved centers. Terminal scoring was not rerun. Playback is scale-ordered for explanation rather than the chronological interleaving of three GPUs. Recording overhead and playback duration do not replace the 8.46 second search measurement. The following slide accounts for the entire traversal.

Transition. Account for every recorded region.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py; docs/presentation-draft/pruning/pruning-trace.npz; docs/presentation-draft/pruning/parity.json; docs/presentation-draft/pruning/movie/movie-manifest.json.

## 28. Recorded search workload

The saved current result accounts for every evaluated region. There are 64,875 evaluations across the hierarchy. Of these, 58,118 are rejected, 6,360 are expanded and 397 become terminal centers. The first identity is their sum. The second identity links root and child work: 39,435 roots plus four children for each of the 6,360 expansions gives the same 64,875 evaluations. These are useful accounting checks and explain why sound region rejection reduces the fine search. They are not a count of independent one-meter point samples, because cells have different scales and terminal scores have a different role. This slide reports the current workload only. It does not substitute a hypothetical exhaustive runtime or a comparison to an earlier implementation.

Transition. Move from search workload to the frozen estimate.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 29. The selected Charleston position

The selected estimate is 2.060304 meters from the chosen simulated observer when measured with an independent WGS84 ellipsoidal calculation. It matches all 64 positive marker reports with consistent status and zero numerical flags. The search did not receive the observer's truth coordinate. The selector first chose a fully matched consistent center with the smallest summed absolute bearing residual, applying fixed east-then-north tie-breaking. That estimate was saved and hashed before the separate evaluator read truth. The summed residual is approximately 6.28098 degrees across all 64 matches; it is not the mean bearing error. The result comes from searching the full declared coast rectangle for this single simulated frame. The following inset shows the remaining local candidates so the selected point does not hide the alternatives.

Transition. Keep the surviving local alternatives visible.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json.

## 30. The remaining local alternatives

The current output contains 148 consistent, 67 inconsistent and 182 unresolved terminal centers. These are tested centers of terminal cells, not a continuous proof that every point inside each rectangle shares its center's status. All unresolved centers lie within approximately 12.1344 meters of the selected estimate. That spread is a description of this output, not a statistical confidence interval or a guaranteed error bound around truth. The unresolved nominal assignments explain 60 to 63 of the 64 reports, while the conservative raw graph still permits possible support. The saved graph does not establish whether every individual case is caused by occluded support, grouping or another retained possibility. No distant coastal alternatives remain in this completed domain, but the local ambiguity still belongs in the presentation. In the local map, the cross marks simulated truth and the diamond marks the selected estimate. The status symbols describe the saved terminal centers.

Transition. Separate the current objective from the proposed mixed-observation study.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz.

## 31. Chart aids versus chart aids plus AIS

This is a proposed paired comparison, not a completed result figure. Both conditions should retain the same map, physical scene, heading assumptions and saved observation frame. The existing condition supplies chart-aid bearings to the deterministic positive-evidence objective. The proposed condition would additionally use observed vessel bearings through a verified combined objective. Removing AIS objects before simulation would be a different intervention because it can change visibility context, angular merging and representative choice. Vessel matching also needs explicit treatment of non-AIS boats, extra vessel reports, stale positions and sensor-to-position offsets. Aid constraints should retain their deterministic positive-only meaning. Until the compatible combined objective is verified and paired runs are authorized, no comparative accuracy or time bars are justified. The current numerical result remains aid-bearing only.

Transition. Return to the timing of the completed current objective.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## 32. Time to locate the observer

The current coast-wide search API took 8.461936 seconds on GPUs zero through two. That is the search-time headline. The number includes device setup and uploads plus coordination and output work performed inside the API; it is not a measurement of GPU kernel busy time alone. The standalone process separately spent 7.388215 seconds reading and validating saved inputs and caches before entering the API. It reused the existing index and did not rebuild it. The complete supervisor pipeline took 23.449189 seconds, including preservation checks, frozen-output selection and separate truth evaluation. Those diagnostic scopes belong in notes or backup. They do not establish initial index-build cost or warm persistent multi-frame throughput. Keeping these boundaries explicit lets the audience understand the measured latency without treating cache loading as repeated index construction.

Transition. Close with the measured capability and declared next work.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; docs/verification/charleston-coastwide-distinct-profiles.zip; docs/resident-search-profiling.md.

## 33. Current capability and next evaluation

The implemented result is a coast-wide search of one simulated Charleston frame using 64 positive aid bearings. It selected a point about 2.06 meters from simulated truth in an 8.46-second search API run on three GPUs. Real NOAA geography and archived AIS define the input scene, while the bearings, observer and sensor effects are simulated. The saved local alternatives remain part of that result. The next work follows directly from the current evidence. Record the exact present traversal for the explanatory animation, strengthen the local visibility or grouping proofs where appropriate, verify a combined vessel-bearing objective, and evaluate a declared set of additional ports. Those are proposed steps, not completed successes. The measured Charleston case supplies a concrete starting point for that evaluation program.

Transition. Invite discussion using the relevant backup detail.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json.

## A1. Bearing and coordinate conventions

This backup separates true north, grid north and the sensor-relative direction on the same saved Charleston geometry. A global bearing is clockwise from true north. For clear Beacon 93428, the WGS84 azimuth from the saved observer is $\beta = 86.6177095761^\circ$. The declared example chooses heading $\psi = 30^\circ$ and therefore relative bearing $\alpha = 56.6177095761^\circ$, using $\beta = (\psi + \alpha)\bmod 360^\circ$. This chosen heading demonstrates the conversion; it is not a recorded vessel heading. The north and heading endpoints in data/heading-demo.json are derived geodesically before projection, so neither arrow should be replaced by an arbitrary map-axis direction. The actual saved reports already contain global bearings, and candidate predictions use WGS84 azimuths. Heading and sensor alignment are upstream assumptions rather than location-search dimensions. Wrapped residuals remain circular across zero degrees.

Transition. Return to the main discussion after resolving this detail.

Sources. runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## A2. Complete sensor specification

The current frame uses seed 11, a ten-kilometer range, full-circle coverage and 0.1-degree angular resolution. The 69 recovered physical-report errors have a maximum absolute value of approximately 0.392584 degrees. Its underlying Gaussian bearing draw has standard deviation one-sixth of a degree and is rejection-sampled within a half-degree bound. There is no deliberate type mislabeling or separate outlier component. Each angular group is detected with probability 0.9, and vessel-only extra reports have Poisson mean one. The saved canonical frame has 200 physical objects within range, 80 clear-visible objects, 79 groups, 69 detected groups and one extra vessel report. That gives 70 reports, with 64 aid bearings used by the solver and six vessel bearings not used as localization observations. These exact counts belong to this frame and must not be replaced with the older 164-report summary.

Transition. Return to the main discussion after resolving this detail.

Sources. docs/verification/charleston-blind-measurements.json; runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json; runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json; maritime/simulator/mismatch.py; maritime/simulator/runner.py; maritime/geometry/angular_grouping.py.

## A3. Whole-cell uncertainty envelope

A whole-cell rejection must hold for every possible observer position in the cell and every object position allowed by its support bound. The envelope therefore combines cell size, chart positional uncertainty, projection and numerical margins. It must widen rather than silently turn unknown support into a precise location. For the true-bearing path, the implementation uses a restricted-domain WGS84 envelope; unsupported cases retain possibilities. The diagram should use an actual candidate cell and actual catalog object, with its expansion identified as a mathematical bound. A compatible edge means that at least one explanation has not been ruled out. It does not assert simultaneous realizability of every retained edge. Failure of the expanded graph is what gives a safe whole-cell impossibility witness.

Transition. Return to the main discussion after resolving this detail.

Sources. maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md.

## A4. Injective feasibility proof

The current rejection proof requires an injective assignment from all 64 positive aid reports to possible canonical objects over the entire cell. Individual degrees and overall candidate count are insufficient. Hall deficiency supplies a concrete obstruction. In the recorded example on main slide 25, reports 38,39 and 40 have exactly two neighbors, so no injection exists despite 1,359 candidate objects in the whole graph. The witness includes all six edges of that subset. Unknown bounds and invalid metadata retain possibilities. The GPU performs the graph construction and matching; a later CPU analysis selected the displayed subset from saved incidence. Main slide 25 provides the visual example, while this backup states the general condition.

Transition. Return to the main discussion after resolving this detail.

Sources. maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md.

## A5. Exhaustive count arithmetic

The rectangular arithmetic uses the actual projected bounds. Ceilings give 1,644,677 by 2,388,460 one-meter grid cells, or 3,928,245,227,420 positions. Multiplying by 64 observations gives a deliberately simplified report-check budget before association and visibility. Under the implemented dyadic refinement rule, 14 halvings of a ten-kilometer cell produce a nominal width of 0.6103515625 meters. Refining every one of the 39,435 roots without rejection would create 10,585,752,207,360 terminal cells and 14,114,336,263,335 total nodes. Those values are calculations, not a brute-force experiment. Dividing work among three GPUs does not remove the need for pruning. Any conversion to runtime would require an explicitly assumed or separately measured throughput with its computational definition.

Transition. Return to the main discussion after resolving this detail.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; tools/run_resident_marker_search.py; maritime/search/gpu/resident/engine.py; maritime/search/gpu/resident/optimized.py.

## A6. Catalog provenance and epochs

The distributed geographic inputs retain source information and are not presented as a synchronized real harbor measurement. The AIS archive contributes 2,268 selected records in the July 15, 2023 one-minute window. The chart extraction occurred in October 2026. Source chart features span scales, and exact-coordinate canonicalization combines chart components without merging nearby distinct points. The resulting 32,168 chart locations retain their source components, including unresolved classification or identity metadata where applicable. Maps should distinguish source class from object class and candidate status. Official website captures provide documentary provenance only; the archived files and their hashes define what the search actually used. A website screenshot must therefore have its own capture date, rather than standing in for the archived service response.

Transition. Return to the main discussion after resolving this detail.

Sources. data/objects-chart-aids-canonical.json.gz; data/objects-ais.json.gz; docs/verification/chart-aid-canonical-catalog.json; runs/charleston-search/20261026T222955253680Z/projected-valid-map.json; data/sources.json.

## A7. Timing definitions

Three different measured intervals appear in the saved record. The standalone process spent 7.388215 seconds loading and validating existing inputs and caches before the search API. It did not rebuild the geographic index. The search API took 8.461936 seconds and includes its device setup, uploads, launches, coordination and output. The complete supervisor pipeline took 23.449189 seconds, adding preservation checks, frozen-output selection and independent truth evaluation. These intervals should not be stacked as three disjoint phases. Per-device CUDA event spans and sampled hardware utilization answer different questions from host wall time. CPU coordination remains part of the application. Initial cache-build cost and steady-state persistent multi-frame throughput are not established by this observation, so neither should be inferred as an unmeasured warm-runtime benefit.

Transition. Return to the main discussion after resolving this detail.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; docs/verification/charleston-coastwide-distinct-profiles.zip; docs/resident-search-profiling.md.

## A8. Remaining unresolved centers

The remaining unresolved centers are genuine saved alternatives, rather than removed outliers. Their nominal grouped assignments match 60 to 63 of the 64 positive reports, but the independent raw-object graph has not proved them impossible. The raw graph preserves possible objects without full polygon sight-line edge removal, and the saved universal rectangular barrier list is empty. The final nominal predictor does apply full shoreline LOS, so the two stages can differ in what they rule out. The archive alone does not assign every unresolved center a unique geometric cause. A future approved per-center diagnostic could expose the relevant visible, blocked and grouped support. Keep the local spread distinct from a probability contour, confidence interval or guaranteed bound on observer error. In the local map, the cross marks simulated truth and the diamond marks the selected estimate. The status symbols describe the saved terminal centers.

Transition. Return to the main discussion after resolving this detail.

Sources. docs/verification/charleston-coastwide-distinct-result.json; docs/verification/charleston-coastwide-distinct-terminal-results.npz; maritime/search/gpu/resident/distinct_pruning.py; maritime/search/gpu/resident/distinct_pruning.cu; maritime/search/gpu/resident/bearing_bounds.cu; docs/resident-pruning-bounds.md.
