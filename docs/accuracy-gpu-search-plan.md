# Accuracy and GPU search implementation plan

J.C. Vaught. October 25, 2026.

This plan prioritizes accurate single-frame localization from global bearings, then reduces coastwide search latency without changing the estimator objective. The recommended system combines geometric proposals, conservative region bounds, exact local association, and refinement of multiple plausible locations. Twenty-four specialist reviews informed the plan. Recommendations are design decisions; no new localization experiments were run for this review.

## Scope and current evidence

The observer is one boat at an unknown two-dimensional position. Its detections contain bearings clockwise from true north, optional object classes, per-detection uncertainty, and optional apparent angular extent. Search receives a timestamped Automatic Identification System (AIS) and chart catalog, a map, measurements, and configuration. It receives no observed-object identity, measured target range, observer position, or coordinates of uncataloged objects. Observer truth and association truth belong only to evaluation.

The maximum viewing distance is a configured envelope, initially 1, 5, or 10 km. Being inside that envelope does not guarantee detection. Land occlusion, angular merging, detection probability, catalog age, and noncatalog detections affect the observation model. Global heading remains known. A calibrated residual heading bias can be an optional nuisance parameter with a bounded prior; it does not introduce an unrestricted heading search.

The authoritative repository inspected for this plan was commit 1feee87ade08e58ed753716e1a2a429b045af2b2 on comech-2080. The server has four RTX 6000 Ada GPUs, each reporting 49,140 MiB. Its topology reports NODE between GPU pairs and no NVLink. Peer access and transfer bandwidth still require measurement. The October 25, 2026 hardware snapshot used nvidia-smi -L, nvidia-smi --query-gpu=name,memory.total --format=csv,noheader, and nvidia-smi topo -m.

| Verified finding | Evidence at inspected commit | Consequence |
| --- | --- | --- |
| Mixed labeled and unlabeled reports can bypass type costs. | bearing_localization/algorithms.py, lines 150–155 build a matrix that solve_group does not consume. | Correctness repair precedes algorithm comparison. |
| Search adapters replace individual bearing uncertainties with an aggregate. | search/grid.py, lines 81–93; search/pairwise.py, lines 421–426; search/randomized.py adapter. | One uncertainty model must reach every scorer and refinement path. |
| Alias handling differs between the legacy scorer and the newer association module. | algorithms.py and search/association.py. | Candidate generation and final assignment must agree on labels. |
| GPU scoring uses independent nearest matches, while production matching enforces one-to-one assignment. | tools/benchmark_eastern_seaboard_gpu.py and tools/benchmark_eastern_seaboard_gpu_search.py. | Existing GPU agreement checks validate a surrogate, not the full estimator. |
| B4's correct tile ranked 35th and was removed by the top-32 aid screen. | results/eastern-seaboard-aid-first/README.md and top32/config.json. | Replace fixed shortlist exclusion with justified bounds or explicit incomplete-search status. |
| The aid screen samples a 10 km lattice inside 100 km tiles. | tools/benchmark_aid_first_tile_pruning.py, lines 87–121. | A sampled minimum is an upper bound on the tile's optimum, not a lower bound that permits pruning. |
| Harbor experiments use 500 m geometry; some older coastwide searches omit land visibility entirely. | data/maps metadata and coastwide benchmark scripts. | Geometry, score, and domain must be held constant for comparisons. |
| Existing bearings are simulated from real AIS and chart coordinates. | tools/run_ais_enc_pilot.py and experiment documentation. | Present results as semi-synthetic until measured bearings and independent observer truth exist. |

The current B4 aid-pruning experiment uses the same East Coast mask throughout. Its confirmed failure is region exclusion; attributing that run to the older mask mismatch would be incorrect. Legacy Chesapeake B1–B5 and the new 24-port B labels need separate identifiers before any combined report.

## Method selection

Rank methods by correctness, localization error and false confident fixes, robustness to real-data errors, full-pipeline latency, then engineering cost. A method cannot win by returning fewer difficult cases or by changing the objective during a speed comparison.

| Priority | Method | Purpose and GPU opportunity | Main limit | Decision |
| --- | --- | --- | --- | --- |
| Required foundation | Correct CPU assignment and exhaustive small-region search. | Reference objective and finite-grid comparison. | Fine coastwide grids are expensive. | Build first and retain as reference. |
| First proposal method | Pairwise reverse-bearing intersections with adaptive sampling. | Batch independent intersections and residual evaluation. | Near-parallel rays, wrong associations, and candidate caps. | Upgrade the existing implementation. |
| First pruning method | Region branch-and-bound using circular bearing intervals. | Batch inexpensive interval and row-minimum bounds. | Bounds must cover the exact objective; weak bounds can be slow. | Prototype alongside proposals with a restricted proven scope first. |
| Second proposal method | Sparse reverse-bearing wedge votes. | Scatter or gather support over spatial tiles. | Duplicate landmark use, discretization, dense accumulators. | Implement only after the first proposal baseline, as a complementary candidate source. |
| Common final stage | Multiple starts with exact reassignment and robust continuous refinement. | Batch residuals; parallel independent modes. | Local convergence cannot restore excluded regions. | Required. |
| Conditional accelerator | GPU assignment, including HyLAC or an audited equivalent. | Many small assignment problems in batches. | Padding, precision, launch costs, integration burden. | Proceed only if assignment dominates profiling. |
| Deferred | FFT correlation, pose fingerprints, approximate nearest-neighbor retrieval. | Fast coarse ranking or reusable static indexes. | Memory, viewpoint dependence, uncertain candidate recall. | Require evidence of a remaining bottleneck before implementation. |
| Deferred | RTX ray tracing of land geometry. | Batched line-of-sight queries. | Mesh construction and precision at shorelines. | Compare only if exact visibility dominates runtime. |

The reviews disagreed about when to implement branch-and-bound. The GPU review favored pairwise proposals and wedge voting because their workloads are regular. The bounds review favored branch-and-bound because it can justify exclusion. The chosen compromise is an early CPU bounds prototype with a bounded scope, developed alongside GPU-friendly proposals. The proposals improve the best known feasible solution, which can make region bounds prune more efficiently. They never define the entire search domain.

The design borrows the bound-plus-local-refinement pattern from global registration research, but Go-ICP solves a different point-cloud objective. Its guarantees do not transfer without new bearing and association bounds [Yang2013GoICP]. PROSAC can improve sampling when correspondence quality is genuinely ranked; a type tag alone does not establish that ranking [Chum2005PROSAC]. HyLAC supplies a relevant batched assignment implementation, not a complete localization algorithm [Nagi2026HyLAC].

## Stage 0 — Freeze semantics and reproduce the failures

Save the current input manifests, source commit, configuration, objective version, candidate budgets, seeds, and site mapping. Preserve existing results as historical artifacts. Create a small diagnostic suite covering mixed labels, heterogeneous uncertainty, duplicated landmark use, north wraparound, empty catalogs, and B4's omitted region.

Define separate names for search completion, modeled objective optimality, actual position error, and association correctness. A finite-grid reference is exhaustive only over that declared grid. A continuous optimum needs a valid bound or another certificate. Neither certificate proves that an imperfect model identifies the actual boat.

Exit gate. The defects have reproducible fixtures; legacy and port site IDs cannot collide; every result reports its objective version and search domain. Search serialization rejects evaluator-only fields. This gate does not require a new coastwide download.

## Stage 1 — Repair the scorer and prepare realistic inputs

The scoring owner creates one estimator objective inside the search branch. Grid, hierarchy, pairwise, randomized search, local refinement, and every backend consume it. The evaluator continues to compute truth-based accuracy independently. Shared contracts and geometry remain reusable libraries; optimization logic does not move into the simulator or evaluator.

Keep a corrected legacy objective for reproducing earlier experiments. Develop a separately versioned likelihood objective for real-data robustness, and rerun all methods when switching objectives. This avoids interpreting a model change as a search speedup.

At a candidate position, the likelihood model uses wrapped angular residuals, each detection's random uncertainty, catalog position uncertainty projected into bearing space, label confidence, and explicit unmatched detections. Include density normalization so a highly uncertain match is not rewarded simply because its normalized residual is small. Apparent angular half-width is target extent, not automatically a Gaussian standard deviation. If a detector returns the center of a visible hull but AIS reports the antenna position, correct the reference point when metadata permit and otherwise propagate offset uncertainty.

Association is one-to-one between reports and predicted observable entities, with a private unmatched option for each report. Unknown type remains compatible; uncertain classifier output becomes a soft type likelihood. Hard incompatibility is reserved for the documented exact-label mode. Conditioned on a candidate's predicted groups and additive costs, assignment provides an exact reference when both report-clutter costs and catalog-object miss/existence costs are represented. Use augmented dummy terms or an equivalent baseline-cost transformation; private unmatched report columns alone do not encode missed catalog objects. Rectangular assignment is suitable when that transformation preserves the complete additive objective [SciPy2026Assignment].

Missed catalog objects contribute only through a declared detection/existence model. A real-data default must not charge every nearby AIS point as certainly visible. Catalog absence creates unmatched observations; an existing but stale or erroneous record has uncertain validity. These are different mechanisms and require separate parameters. Complete reporting remains an explicit synthetic experiment mode.

The ingest owner preserves per-record source time, stable internal identity, raw and normalized class, position quality, valid motion fields, dimensions/reference point where available, and source provenance. Align vessel positions to the observation time using supported interpolation or a motion model with uncertainty. Freeze a maximum report age, interpolation/extrapolation interval and motion-quality rule before evaluation. Exclude out-of-window records or represent their uncertainty under that declared rule. Reject invalid motion sentinels. Exclude the observing boat's own AIS identity for real trials. Synthetic observer placements have no own-AIS identity unless explicitly configured.

For chart aids, preserve feature identity, source layer/cell, edition or retrieval epoch, scale, attributes, physical status, and cross-source duplicate relationships. NOAA recommends selecting one scale band per extraction because multi-band extraction creates duplicates at different resolutions [NOAA2026ENC]. Where a coverage mosaic is necessary, apply an explicit source-priority policy and retain seams and uncertainty. Virtual AIS aids have no physical structure and cannot serve as visual detections [NOAA2026Aids].

The geometry owner establishes true-north geodesic bearings and validated local projected computations. Grid north and true north are not interchangeable. Geographic coordinates remain the reference; local tile coordinates accelerate calculations only within measured error bounds. Fine harbor vector land provides the local line-of-sight reference. Preserve holes, multipolygons, source coverage and shoreline ambiguity.

Exit gate. All search paths agree on fixed-candidate scores and legal assignments under each objective version. Tiny assignment cases match brute-force enumeration. Input timestamps, types, uncertainty and provenance survive program serialization. Geometry fixtures pass narrow-channel, island, hole, tangent, shoreline-endpoint, range-boundary and projection-boundary checks.

## Stage 2 — Establish accuracy references

Use three reference levels. Known-association solves quantify sensor and geometry limitations. Unknown-association exhaustive search on small domains identifies association and optimizer failures. Blind whole-domain search measures proposal and pruning failures. Truth-centered local windows are labeled oracle diagnostics and cannot be reported as blind localization.

Generate both matched-model and mismatch experiments. Matched-model scenes verify software mechanics. Mismatch scenes perturb target positions, heading calibration, catalog completeness, types, map geometry, AIS age, physical extent and detection probability independently of the estimator's assumed values. Use a higher-fidelity or independently prepared visibility reference for map-sensitivity trials. Sharing one approximate mask between generation and search only tests consistency with that mask.

Record association errors and the score at the known position as evaluator diagnostics. These diagnostics never control search. Keep multimodal cases and insufficient-information cases; an arbitrary water-valid location is not a successful fallback.

Exit gate. Deterministic reference outputs, failure rows and objective decompositions exist for hand-checkable cases, the legacy sites and selected development ports. The report identifies whether each error arose from geometry/noise, association, local optimization, candidate generation, or pruning.

## Stage 3 — Build proposals and conservative region search

For a hypothesized match between observation $i$ and catalog object $j$, the observer lies on the reverse bearing ray

$$
\mathbf{x}=\mathbf{L}_j-r\,(\sin\beta_i,\cos\beta_i),\qquad 0<r\le R.
$$

Intersect two such rays for distinct observations and distinct catalog objects. Finite range permits a necessary catalog-pair separation test of at most $2R$, expanded appropriately for coordinate uncertainty. Use spatial indexes to avoid every catalog pair. Preserve the continuous likelihood near the intersection because noisy rays do not make their exact crossing the optimum of the complete observation set.

Replace input-order-sensitive caps with documented streaming budgets and canonical IDs. Weight proposal priority by geometric conditioning and measurement uncertainty. If budgets terminate enumeration, return incomplete-search status and counters rather than a completeness claim. A sampling alternative uses paired correspondences and closed-form initialization before full scoring; it avoids repeated nonlinear fits from random coastwide positions.

Sparse wedge voting broadens each reverse ray by observation uncertainty and accumulates support per observation. Cap an observation's support across alternative landmarks, then apply exact one-to-one matching to candidates. Multiple observations can still claim one landmark in the cheap score, so voting is a proposal score. Keep spatially distinct modes.

For region search, calculate each landmark's possible bearing arc over an entire tile. Include north wraparound; use a full-circle enclosure when a tile contains the landmark or the angular enclosure is uncertain. Compute a lower bound on each allowed edge cost. Summing row minima with unmatched options relaxes one-to-one assignment and yields a cheap lower bound. Solving assignment on the edge lower bounds gives a tighter bound. Evaluate a feasible candidate with the full objective to obtain an incumbent upper bound.

Prune a tile only when its valid lower bound cannot beat the feasible incumbent, allowing for numerical error and ties. A center score is not a region lower bound. All-land exclusion requires the entire tile to be confidently land. Uncertain water or shoreline cells stay eligible.

Start the proof with a fixed-variance, additive objective and fixed catalog identities. Explicitly relax uncertain range and visibility rather than sampling them at tile centers. Extend the certificate only after bounding the complete likelihood, including normalization, negative log-density terms, miss costs and candidate-dependent merging. With variable variance, minimize the whole loss over residual and variance intervals; substituting maximum variance in only the residual term is unsafe.

Angular merging changes the observable entities inside a tile. Until a bound across all possible groupings is proved, such tiles remain unresolved or use a proven relaxation. A full AIS-plus-aid objective also cannot be pruned by an aid-only score without a bound on the omitted contribution. Aid-first processing therefore prioritizes work and generates feasible candidates; global exclusion remains tied to the full chosen objective.

The output includes the best feasible objective, best remaining region bound, unresolved region count, budget saturation, and completion reason. An interrupted run returns a provisional estimate and gap when available. A modeled optimum certificate does not guarantee true-position retention under Gaussian errors or an incorrect catalog.

Exit gate. Bounds have documented derivations and agree with exhaustive tiny cases under their declared scope. Adversarial tests cover negative likelihood costs, uncertainty intervals, angular wrap, near-landmark tiles, soft labels, unmatched reports, merge boundaries and range boundaries. B4's optimum basin cannot disappear because of a fixed rank cutoff. Runtime bounds never consult observer truth.

## Stage 4 — Accelerate the validated workload

Build a conservative range index before optimizing dense all-catalog tensors. Compare sorted uniform cell lists with a CPU spatial-index reference. Static aids have a reusable index; moving AIS uses a separately versioned update path. No cell or neighbor cap can silently discard records. Query halos include the maximum modeled range and coordinate uncertainty.

Batch pair generation, angular likelihoods and region bounds on one GPU first. Keep catalog arrays and static geometry resident; stream bounded batches of hypotheses rather than materializing observation-pair by catalog-pair tensors. Reduce to useful diagnostics and candidate records on device when possible. Use local coordinates and compare floating-point errors against a double-precision reference, especially for near-parallel rays and near-tied assignments.

Initially retain exact CPU assignment for finalists. Compare sparse connected components, size-bucketed CPU solves and a GPU batch solver only after profiling. HyLAC is a candidate because it explicitly supports streams of small assignment problems [Nagi2026HyLAC]. Confirm build compatibility, solver feasibility, objective gaps, rectangular/dummy handling and licensing before adoption. Fractional Sinkhorn assignment is a surrogate; rounding does not turn it into an exact reference.

For visibility, keep indexed vector intersection as the reference. A raster that marks any land-intersecting cell as blocked can preserve blockers but also erase narrow water channels. It must not hard-reject candidates on that basis. Use multistate cells and exact geometry for ambiguous rays. A separate blocker-missing coarse map is optimistic for line of sight but also needs exact final verification. Neither approximation can supply missing-object penalties without the correct conservative direction.

Scale to two and four GPUs by sharding candidate IDs or region nodes, with catalog data replicated where practical. Start with static chunks, then use a dynamic queue to balance dense ports. Publish feasible incumbents at chunk boundaries. Stale incumbents can reduce pruning efficiency but must not alter correctness. Avoid communication per candidate; the measured topology provides no NVLink path. NVIDIA documents topology-dependent peer access, which must be queried rather than assumed [NVIDIA2026MultiGPU].

Exit gate. One, two and four GPUs preserve candidate coverage, legal assignments, objective values within declared tolerances, and equivalent winning modes under ties. Report disagreement rates and objective gaps, not correlation alone. Retain an accelerator only when it improves complete-pipeline latency without violating the accuracy criterion.

## Stage 5 — Benchmark and choose the operating mode

Use separate namespaces for legacy Chesapeake B1–B5 and port-001 through port-024. A proposed 24-port split is 12 development, 4 validation and 8 held-out test ports, stratified by traffic density and harbor geometry. Previously inspected or tuned scenes belong to development; regenerate independent scenes or add sites when that prevents a genuine holdout. Split dates or time blocks as well as locations.

Begin with a manageable development pilot before selecting release sample sizes. Six nominal conditions cover 1, 5 and 10 km with labels enabled and disabled. Preserve the same observation noise draws and scene inputs across compared algorithms. Add controlled partial-label and misclassification trials. Use targeted stress sweeps rather than an enormous full Cartesian product.

| Experiment | Controlled comparison | Required output |
| --- | --- | --- |
| Scorer repair | Legacy versus corrected same-objective implementation. | Type violations, assignment cost, position changes. |
| Likelihood model | Corrected legacy versus calibrated model. | Error, false fixes, uncertainty calibration, associations. |
| Search method | Grid, hierarchy, pairwise, bounds, wedges, combinations. | Objective regret, error, evaluated candidates, completion status. |
| Pruning | No prune versus each exclusion rule. | Reference-optimum retention, true-region retention, bound violations. |
| Visibility | Vector reference versus coarse geometry and mismatch. | Water and ray disagreements, localization consequences. |
| Sensor and catalog stress | Noise, common bias, age, position/reference offsets, omissions, duplicates and wrong types. | Failure curves by perturbation and geometry. |
| GPU scaling | CPU, one GPU, two GPUs, four GPUs. | Complete latency, stage timings, memory, transfer and imbalance. |
| Feature sufficiency | Usable count, angular spread, range and type quality. | Success/error curves and ambiguity; no universal minimum count. |

Report median, 90th and 95th percentile error, maximum error, within-25/50/100/250 m rates, errors exceeding 1 km, no-fix rate, and association precision/recall. Preserve the old 250 m plus three-correct-AIS-match criterion only as a legacy comparison. The new aid-based benchmark reports position success and correct matches across the eligible physical catalog separately, so an aid-only solution is not failed merely for having no vessel matches.

Evaluate posterior or confidence coverage empirically; a score gap is not automatically a probability. Compare exact MAP assignment with adaptive K-best assignment only on ambiguous finalists. A truncated assignment sum and a finite set of pose modes are approximations; record truncation and test sensitivity before reporting normalized confidence.

Reference-optimum retention and truth retention answer different questions. A valid bound can preserve the model optimum while the model prefers an incorrect location. For certified pruning, require proof obligations plus no observed bound violations; empirical tests alone do not prove validity. For heuristic proposals, report recall with uncertainty and retain a full-domain fallback in the accuracy reference mode.

Freeze accuracy noninferiority margins after development and before opening held-out results. Use paired comparisons clustered by port and time; do not treat many noise draws at one site as many independent ports. Select sample size using development variance and the required interval width.

Measure cold startup separately from repeated localization with resident static maps. Per-update latency includes input validation and dynamic updates, coordinate conversion, index work, proposals, bounds, range and water checks, line of sight, assignment, refinement, confidence, transfers, synchronization and output. Report preprocessing/storage separately and amortize it explicitly. The 100 ms objective applies to a complete global fix from an already reported detection set; camera detection latency is an additional budget when that component exists. Do not substitute local tracking or a kernel measurement for this target.

Exit gate. Choose the least costly method that satisfies the predeclared accuracy and false-fix criteria on held-out data. Report whether 100 ms is met at the median and tail, under which workloads, and whether the global search completed. An unmet target remains a result rather than a reason to silently shrink the search area.

## Stage 6 — Validate measured bearings

Collect instantaneous observation sets from one boat with independent Global Navigation Satellite System (GNSS) truth withheld from search. Record calibrated camera intrinsics, boresight, synchronized global heading and attitude, image timestamps, object annotations and extent, observer height, weather, and contemporaneous AIS/chart inputs. Independent identity annotations belong to evaluation.

Start with well-distributed physical landmarks to measure bearing calibration and closure, then include moving ships, uncataloged detections, missed detections and confusing aids. Repeated frames can characterize variance, but held-out locations and times are required to evaluate generalization. Two known nonparallel associations can geometrically determine a two-dimensional position; anonymous matching and real noise require redundancy and ambiguity handling. No second observer station is required.

Keep visibility realism explicit. The 2D land model is an approximation to physical occlusion, and 10 km is an envelope. Height, horizon, buildings and atmospheric visibility become model extensions only when measured residuals show their relevance. Virtual aids are excluded from visual detections; synthetic AIS aids refer to a physical structure but have a remote transmitter [NOAA2026Aids].

Exit gate. Publish measured-bearing localization results separately from simulated-bearing results, using the frozen search program and independently retained observer truth. Report missed and false associations as well as successful fixes.

## Parallel implementation ownership

The 24-agent review is complete. Implementation should use bounded workstreams rather than 24 simultaneous edits to the same scorer. Freeze contracts first, then start independent branches as dependencies permit.

| Workstream | Exclusive primary ownership | Dependency and completion |
| --- | --- | --- |
| A. Contracts and compatibility. | maritime_localization_shared/contracts and serialization. | Stage 0; freeze versions, fields and migration fixtures. |
| B. Exact objective and association. | search/association.py, new search/objective.py, legacy algorithms.py compatibility. | Contract freeze; scorer parity and tiny assignment reference. |
| C. Data normalization. | Ingest adapters and acquisition/preprocessing tools. | Contract freeze; timestamp, classes, provenance and duplicate policy. |
| D. Geometry and coordinates. | Shared geospatial library and map conversion tools. | Contract freeze; vector reference and conservative query semantics. |
| E. Reference and evaluation. | evaluation package and dedicated benchmark tools. | Frozen objective API; accuracy metrics and withheld-truth protocol. |
| F. Pairwise proposals and refinement. | search/pairwise.py, randomized proposal adapter, new refinement module. | Objective API; coverage instrumentation and multistart tests. |
| G. Region bounds. | New search/regions.py and search/bounds modules. | Objective API and geometry contract; derivations and bound tests. |
| H. Wedge proposal experiment. | Isolated search/proposals/wedge module. | Reference cases and candidate API; stop if redundant or inferior. |
| I. GPU backend. | Search backend and GPU batch modules. | Objective parity gate; sparse costs, precision and assignment profiling. |
| J. Orchestration and release. | main.py, CLI integration, CI and documentation. | Integrates accepted branches and verifies independent program handoffs. |

Each workstream owns its tests and does not edit another workstream's core files. The coordinator controls interface migrations and conflict resolution. Simulator, search and evaluator remain independently runnable. The main entry point composes them through versioned handoffs; ingest sits outside their computational branches. Shared code contains contracts, coordinates and reusable geometry, while search owns optimization and evaluation owns truth metrics.

Use short workstream branches and small reviewable commits. Push each completed logical milestone. Integrate continuously, then reserve a dedicated repair wave for contract migrations, geometry disagreement, scoring parity, truth isolation, CLI behavior and benchmark reproducibility. Run focused checks after each relevant merge; run the full suite, formatting, lint, type checks and existing figure builds at the integration gate. The existing .gitignore already excludes environments and build caches while preserving publication outputs.

## Specialist review ledger

All 24 specialists were asked to analyze and propose without modifying software or running experiments. The final six assignments provided independent review or integration planning rather than duplicate implementation.

| Agent | Assigned responsibility | Result incorporated |
| --- | --- | --- |
| 01 | Scoring correctness. | Mixed-label defect and incompatible scorer paths. |
| 02 | Measurement uncertainty. | Width versus sigma, shared bias and AIS reference-point offsets. |
| 03 | Data association. | Exact MAP baseline, clutter/misses and conditional K-best refinement. |
| 04 | Pairwise and randomized methods. | Cap/order bias, degeneracy, indexed scoring and better initialization. |
| 05 | Pruning audit. | B4 rank-35 exclusion and center-score bound error. |
| 06 | Geometry and visibility. | Vector reference, conservative tile summaries and map uncertainty. |
| 07 | AIS and chart alignment. | Time, type, identity, duplicate and source-epoch handling. |
| 08 | Existing GPU code. | Surrogate-versus-production objective gap and timing omissions. |
| 09 | Branch-and-bound derivation. | Circular interval and assignment-relaxation bounds. |
| 10 | Reverse-bearing voting. | Correct reverse-ray geometry and per-observation support. |
| 11 | Signature indexes. | Viewpoint dependence and pose-index storage/recall tradeoffs. |
| 12 | FFT and distance transforms. | Correlation proposals, halos, memory and quantization limits. |
| 13 | GPU assignment. | Batch solver candidates, dummies, precision and crossover tests. |
| 14 | Multiple GPUs. | Hardware topology, disjoint candidate ownership and deterministic merge. |
| 15 | Spatial indexing. | Sorted cell lists, dynamic AIS overlays and uncapped neighbors. |
| 16 | GPU line of sight. | Raster/vector/RTX tradeoffs and boundary precision. |
| 17 | Observability. | Range and bearing geometry limits, bias and feature-count curves. |
| 18 | Benchmark design. | Holdouts, reference ladder and accuracy versus latency measurement. |
| 19 | Architecture. | Program boundaries, migration and exclusive ownership. |
| 20 | Mathematical red team. | Full-likelihood bounds, negative terms and merging failures. |
| 21 | GPU method review. | Practical proposal-first ranking and conditional assignment acceleration. |
| 22 | Real-world red team. | Sensor calibration, physical aid semantics and field validation. |
| 23 | Integration gates. | Lean dependency sequence and stop criteria. |
| 24 | Alternative-method red team. | Valid RANSAC/PROSAC transfer and limits of registration/temporal methods. |

Review corrections matter. Reverse voting was checked against the unknown-observer formulation, and a proposed two-observer field trial was rejected because it solved a different problem. Majority agreement alone is not the method-selection criterion. Bounds require derivations, libraries require capability checks, and speed claims require measurements on the declared workload.

## Stop criteria and release deliverables

Stop a proposed accelerator if it changes the objective, silently drops eligible neighbors, relies on unavailable ranges or identities, or creates false confident fixes beyond the predeclared margin. Defer a valid but loose bound if its work exceeds the search it saves. Defer FFT, approximate fingerprints and RTX geometry until a measured bottleneck justifies them. Stop multi-GPU expansion if transfer, CPU assignment or scheduling costs erase its benefit.

Release artifacts include a versioned objective specification, data and map manifests, a defect regression suite, reference cases, a pruning proof and scope statement, reproducible benchmark commands, trial-level results, stage timings, confidence/failure tables, and a selected operating configuration. Figures will be authored in Typst with CeTZ or Lilaq. Required figures compare error distributions, pruning retention, accuracy versus latency, feature count versus geometry, one/two/four-GPU scaling, and correct/incorrect/ambiguous port examples. This planning change adds no generated performance figures.

## References

Bibliographic records are centralized in ../references.bib. Links below identify the primary sources used for design decisions.

| Key | Source and supported use |
| --- | --- |
| Yang2013GoICP | [Global registration with bounds and local refinement](https://www.cv-foundation.org/openaccess/content_iccv_2013/papers/Yang_Go-ICP_Solving_3D_2013_ICCV_paper.pdf). Architectural precedent, not a direct maritime solver. |
| Chum2005PROSAC | [Progressive sample consensus](https://cmp.felk.cvut.cz/~matas/papers/chum-prosac-cvpr05.pdf). Ranked-correspondence sampling and its assumptions. |
| Nagi2026HyLAC | [Hybrid Linear Assignment solver in CUDA](https://github.com/Nagi-Research-Group/HyLAC). Batched GPU assignment candidate. |
| SciPy2026Assignment | [Rectangular linear assignment](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linear_sum_assignment.html). CPU reference capability. |
| NVIDIA2026MultiGPU | [Multiple GPU programming](https://docs.nvidia.com/cuda/cuda-programming-guide/03-advanced/multi-gpu-systems.html). Topology and peer-access requirements. |
| NOAA2026ENC | [ENC Direct to GIS](https://www.nauticalcharts.noaa.gov/learn/encdirect/). Scale selection, object layers and chart provenance. |
| NOAA2026Aids | [AIS aids to navigation](https://nauticalcharts.noaa.gov/publications/portrayal-of-ais-aids-to-navigation.html). Physical, synthetic and virtual aid distinctions. |
| NOAA2026AISDictionary | [AIS data dictionary](https://coast.noaa.gov/data/marinecadastre/ais/data-dictionary.pdf). Source fields and normalization. |
| NOAA2026AISFAQ | [AIS archive FAQ](https://coast.noaa.gov/data/marinecadastre/ais/faq.pdf). Coverage and temporal limitations. |
| USCG2026AISEncoding | [AIS encoding guide](https://www.navcen.uscg.gov/sites/default/files/pdf/AIS/AISGuide.pdf). Position reference and vessel dimensions. |
