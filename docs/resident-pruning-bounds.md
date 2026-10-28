# Independent chart proofs and true-bearing envelopes

The opt-in implementation leaves the running baseline, prepared input layout and saved cache unchanged. It separates proofs based on chart completeness from proofs based on shoreline coverage. A complete chart catalog can prove that no marker could explain a positive detection even where shoreline coverage is unknown. Unknown shoreline can obstruct an object, but cannot create a missing charted object. Whole-cell land rejection still requires reliable land coverage, and an inconclusive sight line stays unresolved.

The implementation never uses a missed vessel or undetected chart aid as negative evidence. Unknown object types remain possible chart objects. Count pruning additionally requires distinct physical identities. Unbounded positions, incomplete chart scope and missing numerical certificates disable the affected hard proofs. The independently certified chart polygon must cover the complete cell visibility envelope, including bounded position offsets represented by the object-support certificate.

## Removing the fixed projection-angle allowance

The original region predicate compares a projected-grid angle with a true-north observation and adds a fixed 30-degree allowance. The new predicate computes the true WGS84 bearing from the cell center to each possible object, then encloses how much that bearing can change over the complete cell and object-position bound. It also uses the corresponding true-distance lower bound to remove objects that cannot be in range.

The geometric foundations are the reduced length and both geodesic scale factors defined in [GeographicLib](https://geographiclib.sourceforge.io/html/python/geodesics.html) and [Karney, Algorithms for geodesics](https://arxiv.org/abs/1109.4448). The following envelope is a derived comparison bound reviewed for this implementation. It is not a bound quoted from those sources.

Let the projected cell circumradius be $r_p$, the accepted lower projection-scale bound be $k_{\min}$, the inverse-projection position error bound be $\epsilon_p$, and the object's hard ground-distance uncertainty radius be $U$. A standard deviation is not an admissible substitute for $U$. The observer and total endpoint-motion radii are

$$
R = r_p/k_{\min} + 2\epsilon_p, \qquad S = R + U.
$$

The kernel uses $k_{\min}=0.99$ within the restricted projection domain below and $\epsilon_p=0.01$ m. For a computed center-to-object geodesic distance $\hat d$, use the numerical distance bound to form $d_-=\hat d-\epsilon_d$ and $d_+=\hat d+\epsilon_d$. The object's possible range is bounded below by $\max(0,d_- - S)$.

For WGS84, let $a=6378137$ m and $M_{\min}=a(1-e^2)$. The Gaussian curvature is positive and at most $1/M_{\min}^2$. The geodesic Jacobi equation gives the reduced-length lower bound $m(r)\ge r-r^3/(6M_{\min}^2)$ for distances at most 100 km. Forward and reverse geodesic scale factors remain positive and at most one over that interval. Therefore the endpoint-motion azimuth sensitivities are at most $1.001/r$. Integrating over total endpoint motion $S$ gives the logarithmic term below.

The geographic north frame also changes as the observer moves. Its connection has magnitude $|\sin\phi\,d\lambda|\le |\tan\phi|\,ds/a$. Thus the observer path contributes at most $R\tan\phi_{\max}/a$, where $\phi_{\max}=|\phi_c|+R/M_{\min}$. The complete angular envelope in radians is

$$
\Delta = 1.001\log\!\left(\frac{d_-}{d_- - S}\right)
       + R\tan(\phi_{\max})/a.
$$

The code evaluates the logarithm as log1p(S/(d_lower-S)). It adds the accepted numerical bearing error and retains the configured sensor tolerance and object extent separately. The envelope shrinks as the cell shrinks; it has no fixed 30-degree floor.

If the projection or geodesic computation fails, a bound is nonfinite, $d_-\le S$, $d_++S>100$ km, $\phi_{\max}>60$ degrees, the projection domain is unsupported, or the cone reaches 180 degrees, that object retains all angular possibilities. No region is rejected by an unproven angular calculation.

## Restricted inverse-projection scale argument

The implementation supports only EPSG:32618 and cells fully within easting [-500000,1500000] m and northing [0,6000000] m, with the latitude guard above. [PROJ's transverse Mercator equations](https://proj.org/en/stable/operations/projections/tmerc.html) describe the conformal-sphere and Krueger rectification structure. [Karney's transverse Mercator paper](https://arxiv.org/abs/1002.1417) supplies the numerical-series foundation. Their accuracy statements do not automatically certify this CUDA implementation.

For the coded sixth-order inverse rectification, the complex derivative magnitude is bounded by $1+\sum_{j=1}^6 2j|\beta_j|\cosh(2j\eta_{\max})<1.002$ when $|\eta|\le0.16$. The coded easting limits imply that range. The inverse spherical-TM angular scale is at most one. The conformal-sphere to ellipsoid ground scale is bounded above by $N_{\max}$, because $|\chi|\le|\phi|$ on an oblate ellipsoid. Using $N_{\max}\le a/0.9966$, $A\ge a/1.00168$ and $k_0=0.9996$ gives inverse ground Lipschitz factor

$$
L \le \frac{1.002(1.00168)}{0.9966(0.9996)} < 1/0.99.
$$

Numerical inverse-position, distance and bearing bounds remain explicit accepted premises. The witness tests check the implementation against independent geodesic calculations; finite witness tests are not an exhaustive floating-point certificate over the entire coast.

## Parallel calculation changes

Parallel prediction retains the existing bounded spatial gather order, then evaluates geodesics and sight lines across candidate/object pairs. Stable device sorting and group reductions preserve circular grouping, frontmost representatives, extents and uncertainty flags. Spatial gathering and exact capacity-prefix handling still contain serial work per candidate. A dense overflow case may perform more visibility work than the old early-exit predictor, so it is included in the short comparison.

The exact assignment uses a cooperative warp for each candidate's column scans and potential updates. All-infinite columns are removed with a stable GPU compaction and the selected indices are mapped back to original groups. Zero-edge candidates return exact unmatched outputs. Partial assignments retain maximum cardinality and minimum matched-bearing residual; a count deficiency does not permit arbitrary replacement of partial matches. Sequential augmentation dependencies remain, while column work and separate candidates run in parallel.

The short benchmark uses identical saved inputs, fixed candidate cells and capacities, separate warmups, synchronized timings and alternating execution order. GPU 3 has another job, so its times are shared-device observations rather than an uncontended hardware speed claim. No optimized full-coast search replaces the existing run automatically.

## Opt-in use and remaining serial work

`maritime.search.gpu.resident.optimized.OptimizedResidentDevice` implements the same seed, prune, split and score device API as `ResidentDevice`. Its constructor enables independent chart pruning, the true-bearing envelope, parallel prediction and cooperative matching by default. Each feature also has an explicit constructor switch for an ablation. `tools/compare_resident_optimization.py` exercises that API against the original implementation using the saved full-coast cache, and refuses devices 0, 1 and 2 while the existing run owns them.

The running multi-device search and its CLI still select the original `ResidentDevice`. This change does not switch a live process to new kernels. Adopting the optimized implementation for another coast-wide run is a separate launch step after review. Both variants load the same prepared arrays; none of the optimization modules changes preparation, the cache layout, its key, or the baseline engine and kernels.

The optimization parallelizes candidate/object geodesics and visibility, circular-group reductions, and assignment column scans. It does not eliminate all serial dependencies. Spatial-tree traversal, capacity-prefix handling, each assignment's successive augmentations, and Python launch orchestration remain. Two aggregate allocation sizes also synchronize with the host. CPU scheduling, synchronization and disk output continue alongside GPU numerical calculation.

The new verification consists of 28 focused tests across chart pruning, cooperative matching, parallel prediction and combined scoring. Whole-cell witnesses include corners and interior observer positions, bounded object-position offsets and unsupported-domain fallbacks. Matching cases include randomized independent assignments, forbidden edges, partial cardinality, padding and certainty-sensitive ties. These tests passed with the independent implementation reviews.

## Short comparison recorded on October 28, 2026

The [complete timing and parity record](verification/resident-optimization-short.json) uses the saved coast-wide cache with a preparation builder that raises if called. Each variant receives the same observations and inputs. Input creation is explicitly synchronized before handing arrays to non-blocking resident streams. Compilation and warmups are separate from three alternating timed repeats. The figures below are median synchronized wall times for the measured calls, excluding cache loading and device setup.

| Workload | Original | Optimized | Work/result comparison |
| --- | --- | --- | --- |
| Four fixed Charleston candidate scores | 6.842661 s | 0.055668 s | Equal status, flags, matched cardinalities and partial residual objective |
| Eight-root search, 10 km to 2.5 km | 2.481571 s | 0.021867 s | 168 evaluated regions and 128 leaves become 8 rejected roots and no leaves |
| Prune 256 fixed coast zones | 0.070367 s | 0.126941 s | 148 rejected / 108 retained become 246 rejected / 10 retained |
| Dense scoring with capacity 8 | 0.737327 s | 0.057102 s | Equal unresolved status, flags and partial cardinalities |

The fixed-batch pruning kernel is slower. Its benefit in this test is stronger exclusion before expensive descendants and terminal scores. The eight-root workload lies away from the true Charleston witness and demonstrates reduced traversal work, rather than equal-work kernel speed or successful localization. The fixed scoring comparison demonstrates faster calculation of equivalent partial results.

GPU 3 was shared with another workload reporting 100 percent utilization before this run. Contention changes during the paired samples. These are observed shared-device timings, not isolated hardware measurements, a coast-wide speed multiplier, or a whole-coast completion estimate.

All five cells containing the true Charleston position, with widths 1, 10, 100, 1000 and 10000 meters, survived the optimized pruning. However, all four terminal scoring probes remain unresolved with numerical-boundary flag 8. The true-position probe matches only 47 of 89 reports, and the other probes match 37, 26 and 21. Both implementations have identical partial objectives and cardinalities. This establishes parity for this workload, not a correct Charleston localization. The prediction/visibility mismatch needs a separate correction before accuracy can be claimed.

The cache was loaded without reconstruction. Its archive size and nanosecond modification time were identical before and after the comparison; this check is not a content hash. Baseline engine, CUDA kernels, preparation and assignment source hashes were identical. The original search process remained alive and its evaluated-region counter advanced during the comparison. No production settings or existing results were modified.

A separate [single-position visibility diagnosis](verification/charleston-witness-visibility.json) found that all 43 missing expected marker representatives and every member of those groups exist as chart aids in the cache, but resident GPU sight lines classify them as blocked. The simulator uses the same named map and only emits clear targets. This isolates a reference-versus-GPU visibility disagreement, without establishing which implementation is wrong. Numerical-boundary flag 8 is independent of those blocked rays. Correcting that disagreement requires a targeted geometry follow-up; the performance changes do not conceal or repair it.
