# A22 screened finite-candidate search

Status: implemented and pushed. A06 review permits supported pruning over a declared finite candidate set; it does not establish continuous optimality.

Branch: `accuracy/a22-screened`.

Latest controller commit: `43cc6ab901c54c56d198b7d611516660b87286ee`.

This branch includes A14 hybrid batch screening and final metadata hardening, based on the visibility screen and A21 objective-bound prerequisites.

## Owned changes

`bearing_localization/search/screened_search.py` provides deterministic serial candidate traversal. Each candidate is either evaluated by the existing `CandidateScorer` or omitted from exact scoring only after a supported prepared objective lower bound and a complete LOS/model coverage scan certify the prune. The controller does not change candidate generation, the command line, configuration, or continuous search behavior.

`tests/accuracy/test_a22_screened.py` compares screened winners and tied minima with direct full enumeration on small water, land, and unknown-coverage scenes, with type labels on and off. It checks negative objective values, bridge-connected bearings, every pruned bound against independent exact scoring, seed-incumbent revalidation, budget accounting, resumable iterator remainder, unsupported-objective fallback, and unknown LOS behind a clear member.

## Public API

```python
@dataclass(frozen=True, slots=True)
class CandidatePosition:
    candidate_id: str
    position_xy_m: tuple[float, float]

@dataclass(frozen=True, slots=True, init=False)
class FeasibleIncumbent:
    candidate_id: str
    position_xy_m: tuple[float, float]
    evaluation: CandidateEvaluation

@dataclass(frozen=True, slots=True)
class ScreenedSearchResult:
    localization: LocalizationResult
    visits: tuple[CandidateVisit, ...]
    candidate_count: int
    exact_scored_count: int
    exact_evaluation_count: int
    invalid_water_count: int
    unresolved_count: int
    pruned_count: int
    unvisited_count: int | None
    unvisited_candidate_ids: tuple[str, ...]
    first_unvisited_candidate_id: str | None
    best_scored_candidate: FeasibleIncumbent | None
    total_elapsed_s: float
    visibility_elapsed_s: float
    bound_elapsed_s: float
    exact_score_elapsed_s: float
    incumbent_validation_count: int
    incumbent_validation_elapsed_s: float
    remaining_candidates: Iterable[CandidatePosition]

def screened_candidate_search(
    scorer: CandidateScorer,
    candidates: Iterable[CandidatePosition],
    *,
    visit_budget: int,
    complete_candidate_count: int | None = None,
    feasible_incumbent: FeasibleIncumbent | None = None,
    comparison_margin: float | None = None,
) -> ScreenedSearchResult
```

A supplied incumbent is validated by a fresh exact `scorer.evaluate` call at its position; its previous floating score is not trusted. The resulting validation count and time are reported separately from visited-candidate scores. The incumbent type has no public initializer.

For a generic iterator that reaches its visit budget, `remaining_candidates` begins with the first unvisited item and continues the same stream. Callers should resume from this returned iterable. The exact unvisited count is reported when the stream length is declared; otherwise it remains unknown and the first unvisited ID is still recorded.

## Pruning and certificate scope

A prepared A21 bound is built once for the measurement/configuration pair, with variance support enabled only for the covered diagonal finite-sigma regime. For each eligible water candidate, the controller runs one complete A14 visibility scan and derives the bound from the certified visible-group count. This avoids doing a scalar partial traversal before the complete hybrid scan. The complete scan checks every physical member, so an unknown far member or unsupported model record cannot be hidden behind an already-visible bridge member. If full visibility cannot be certified, the unchanged exact scorer runs and its unresolved result remains in counts. Known land and unknown-water candidates bypass visibility screening and go directly through CandidateScorer, which records their canonical invalid-water or unresolved-coverage status. The comparison margin is never smaller than the configured absolute plus score-scaled relative tolerance. Tied minima are retained. The finite-candidate certificate is returned only after the entire stream is consumed and no exact-scored candidate has unresolved coverage or model support. Budget exhaustion returns `budget_exhausted`, retains the continuation and unvisited diagnostics, and does not produce a finite-set certificate. The controller does not issue a continuous-objective certificate or accept a location.

A full scan is required before a bound can exclude a candidate. The controller therefore favors one complete hybrid scan over partial screening followed by a repeated full scan; it does not alter the bound or scoring rules. No performance claim is made until the same 255-candidate timing comparison is run under the shared benchmark lock.

## Verification

Python 3.11.15 was used.

- `uv sync --locked --all-groups`: passed.
- Scoped Ruff format/check: passed.
- Scoped `ty check`: passed.
- `uv run pytest -q tests/accuracy/test_a22_screened.py`: 13 passed.
- `uv run pytest -q tests/accuracy/test_a18* tests/accuracy/test_a20* tests/accuracy/test_a21* tests/test_architecture_boundaries.py`: 41 passed.
- `git diff --check`: passed.
- `git push origin accuracy/a22-screened`: passed.

The controller remains serial and limited to the supplied finite candidate stream; global completeness across multiple chunks needs a caller-maintained candidate ledger.


## Same-sample serial timing

The same 255 saved cache-order candidates were evaluated by direct exact scoring and by the A22 serial controller at `204ca2e`. The timing harness was committed before measurement at `d3d09da`. It used the frozen map-projected positions, measurements, catalog, and scoring configuration from the cached-profile artifacts. Setup, including contract loading, map construction, and scorer preparation, took 13.425 s and is reported separately.

| Mode | Wall time (s) | Exact evaluations | Exact-scored | Pruned | Invalid water | Unresolved coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct exact | 18.238 | 255 | 131 | 0 | 111 | 13 |
| A22 screened | 23.025 | 170 | 46 | 85 | 111 | 13 |

Both modes returned best score `24.468390694319403` with tie ID `cell:74:2578`. All 85 pruned candidates had supported bounds no greater than their independently measured exact scores. The screened run took 26.2% longer than direct exact scoring on this sample, despite reducing exact scoring by 85 candidates. This measurement does not show a runtime benefit from screening.

All four numerical-library thread settings were fixed to one thread. Input and scorer setup were outside the timed scoring intervals. Direct exact scoring ran first, followed by screened scoring, under a 600 s watchdog and the canonical nonblocking benchmark lock. The host was active during both runs. The lock-time activity snapshot recorded load averages of `10.09` before timing and `8.46` after timing, active CPU processes, and two GPU processes using approximately 24.6 and 25.0 GiB. Peak resident memory for the harness process was 1,223.8 MiB. Treat the wall times as shared-host diagnostic measurements; they do not establish clean-host performance.

The raw result, including every selected ID and position, per-candidate exact and screened action, score, lower bound, parity checks, configuration and selection hashes, thread settings, and activity snapshots, is [result.json](experiments/a22-screened-same255/result.json). The recorded candidate selection is identified by SHA-256 `d4f2fa4812855a2f691a2ab7e9ad5035623c63abc8192106878dfd879d901a3e`.
