from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace

import pytest

from accuracy.test_a21_pre_los import _measurements, _prepared_bundle, _records
from bearing_localization.search.config import SearchConfig
from tools.profile_pre_los_bound import (
    CandidatePoint,
    independently_validate_prunes,
    load_truth_free_expanded_incumbent,
    pre_los_screen,
)


def _scorer(*, config: SearchConfig | None = None):
    measurements = _measurements()
    selected_config = config or SearchConfig(
        max_range_m=1_000.0,
        circular_merge_tolerance_deg=0.5,
        expected_unmatched_reports=1.0,
    )
    prepared = _prepared_bundle(_records(), measurements, selected_config)
    return prepared._scorer, prepared.candidate_xy_m


def test_pre_los_screen_prunes_only_supported_safe_points_and_preserves_exact_tie():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("same-pose-tie", seed_position),
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
        CandidatePoint("offset-200", (seed_position[0] + 200.0, seed_position[1])),
    )

    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    exact = {item.candidate_id: scorer.evaluate(item.position_xy_m) for item in candidates}

    assert [item.candidate_id for item in dispositions] == [
        item.candidate_id for item in candidates
    ]
    assert dispositions[0].action == "exact_incumbent"
    assert dispositions[1].action == "exact_scored"
    assert exact["same-pose-tie"].hypothesis is not None
    assert math.isclose(
        exact["same-pose-tie"].hypothesis.score,
        seed.hypothesis.score,
        abs_tol=1e-8,
        rel_tol=1e-10,
    )
    pruned = [item for item in dispositions if item.action == "proposed_prune"]
    assert pruned
    for item in pruned:
        evaluation = exact[item.candidate_id]
        assert item.preparation_supported is True
        assert item.coverage_certified is True
        assert item.bound_supported is True
        assert item.lower_bound is not None
        assert evaluation.status == "scored" and evaluation.hypothesis is not None
        assert item.lower_bound <= evaluation.hypothesis.score + 1e-8
        assert not math.isclose(
            evaluation.hypothesis.score,
            seed.hypothesis.score,
            abs_tol=1e-8,
            rel_tol=1e-10,
        )


def test_unsupported_pre_los_bound_falls_back_to_exact_score(monkeypatch):
    import tools.profile_pre_los_bound as profile_module

    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.status == "scored" and seed.hypothesis is not None

    class UnsupportedBound:
        bound = None
        supported = False
        reason = "unsupported test"

    monkeypatch.setattr(
        profile_module,
        "pre_los_catalog_row_min_bound",
        lambda *_args, **_kwargs: UnsupportedBound(),
    )
    candidate = CandidatePoint("unsupported-bound", (seed_position[0] + 100.0, seed_position[1]))
    dispositions, _ = pre_los_screen(
        scorer,
        (CandidatePoint("seed", seed_position), candidate),
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )

    visit = dispositions[1]
    assert visit.action == "exact_scored"
    assert visit.bound_supported is False
    assert visit.score is not None
    assert visit.exact_elapsed_s >= 0.0


def test_seed_must_match_exact_score_and_duplicate_ids_fail_closed():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.hypothesis is not None
    one = (CandidatePoint("seed", seed_position),)
    with pytest.raises(ValueError, match="revalidated incumbent score"):
        pre_los_screen(
            scorer,
            one,
            incumbent_id="seed",
            saved_incumbent_score=seed.hypothesis.score + 1.0,
        )
    with pytest.raises(ValueError, match="unique"):
        pre_los_screen(
            scorer,
            (one[0], one[0]),
            incumbent_id="seed",
            saved_incumbent_score=seed.hypothesis.score,
        )


def test_post_pass_rejects_forged_overlarge_bound():
    scorer, seed_position = _scorer()
    seed = scorer.evaluate(seed_position)
    assert seed.hypothesis is not None
    candidates = (
        CandidatePoint("seed", seed_position),
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
    )
    dispositions, _ = pre_los_screen(
        scorer,
        candidates,
        incumbent_id="seed",
        saved_incumbent_score=seed.hypothesis.score,
    )
    pruned = next(item for item in dispositions if item.action == "proposed_prune")
    with pytest.raises(AssertionError, match="bound exceeds exact score"):
        independently_validate_prunes(
            scorer,
            (replace(pruned, lower_bound=1.0e12),),
            exact_best_score=seed.hypothesis.score,
        )


def test_isclose_tie_margin_covers_relative_tolerance_for_negative_best():
    from tools.profile_pre_los_bound import _isclose_tie_margin

    best = -10_000.0
    margin = _isclose_tie_margin(best, abs_tol=1e-8, rel_tol=1e-3)
    candidate_at_edge = best + margin * 0.99
    candidate_beyond_edge = best + margin * 1.01
    assert math.isclose(candidate_at_edge, best, abs_tol=1e-8, rel_tol=1e-3)
    assert not math.isclose(candidate_beyond_edge, best, abs_tol=1e-8, rel_tol=1e-3)


def test_external_incumbent_is_revalidated_and_outside_candidate_accounting():
    scorer, seed_position = _scorer()
    external = CandidatePoint("expanded-winner", seed_position)
    saved = scorer.evaluate(seed_position)
    assert saved.status == "scored" and saved.hypothesis is not None
    candidates = (
        CandidatePoint("offset-100", (seed_position[0] + 100.0, seed_position[1])),
        CandidatePoint("offset-200", (seed_position[0] + 200.0, seed_position[1])),
    )
    dispositions, summary = pre_los_screen(
        scorer,
        candidates,
        saved_incumbent_score=saved.hypothesis.score,
        external_incumbent=external,
    )
    exact_candidates = {
        item.candidate_id: scorer.evaluate(item.position_xy_m) for item in candidates
    }
    exact_best = min(
        [saved.hypothesis.score]
        + [value.hypothesis.score for value in exact_candidates.values() if value.hypothesis]
    )
    pruned = [item for item in dispositions if item.action == "proposed_prune"]
    independently_validate_prunes(scorer, pruned, exact_best_score=exact_best)
    assert [item.candidate_id for item in dispositions] == [
        item.candidate_id for item in candidates
    ]
    assert summary["incumbent_outside_candidate_set"] is True
    assert summary["candidate_count"] == len(candidates)
    assert summary["incumbent_candidate_id"] == external.candidate_id
    assert external.candidate_id in summary["exact_results"]
    assert all(item.action != "exact_incumbent" for item in dispositions)
    assert {item.candidate_id for item in dispositions} == set(exact_candidates)
    with pytest.raises(ValueError, match="revalidated incumbent score"):
        pre_los_screen(
            scorer,
            candidates,
            saved_incumbent_score=saved.hypothesis.score + 1.0,
            external_incumbent=external,
        )
    with pytest.raises(ValueError, match="position must be outside"):
        pre_los_screen(
            scorer,
            (CandidatePoint("same-pose", seed_position),),
            saved_incumbent_score=saved.hypothesis.score,
            external_incumbent=external,
        )


def test_truth_free_expanded_loader_binds_inputs_and_scoring_semantics(tmp_path):
    config = SearchConfig(
        max_range_m=1_000.0,
        circular_merge_tolerance_deg=0.5,
        expected_unmatched_reports=1.0,
    )
    config_dict = {key: getattr(config, key) for key in config.__dataclass_fields__}
    config_dict.update({"candidate_budget": 256, "bounds_xy": [-1, 1, -1, 1]})
    config_path = tmp_path / "source-config.json"
    config_path.write_text(json.dumps(config_dict, sort_keys=True))
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in (
                {
                    "completed": True,
                    "candidate_id": "higher",
                    "best_hypothesis": {"score": 2.0, "east_m": 10.0, "north_m": 20.0},
                },
                {
                    "completed": True,
                    "candidate_id": "winner",
                    "best_hypothesis": {"score": 1.0, "east_m": 30.0, "north_m": 40.0},
                },
            )
        )
        + "\n"
    )
    static_hashes = {"measurements.json": "m", "catalog.json": "c", "map.json": "x"}
    manifest = {
        "schema": "assignment-expansion-diagnostic-v1",
        "truth_used": False,
        "evaluator_used": False,
        "rows_sha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(),
        "config_path": str(config_path),
        "source_pins": {
            "config_path": str(config_path),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "attempt_tree_bindings": {
                "identity": {
                    "static_input_sha256": {"measurements": "m", "catalog": "c", "map": "x"}
                }
            },
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    active_config = dict(config_dict)
    active_config["candidate_budget"] = 1
    point, score, provenance = load_truth_free_expanded_incumbent(
        manifest_path,
        rows_path,
        expected_static_hashes=static_hashes,
        active_config=active_config,
        expected_score=1.0,
    )
    assert point == CandidatePoint("winner", (30.0, 40.0))
    assert score == 1.0
    assert provenance["source_truth_used"] is False
    assert provenance["source_evaluator_used"] is False
    assert provenance["scoring_config_projection_matches"] is True
    assert provenance["search_only_config_differences"]["candidate_budget"] == {
        "source": 256,
        "profile": 1,
    }
    active_config["detection_probability"] = 0.8
    with pytest.raises(ValueError, match="scoring objective/config differs"):
        load_truth_free_expanded_incumbent(
            manifest_path,
            rows_path,
            expected_static_hashes=static_hashes,
            active_config=active_config,
            expected_score=1.0,
        )
