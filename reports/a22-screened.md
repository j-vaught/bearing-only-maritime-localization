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
