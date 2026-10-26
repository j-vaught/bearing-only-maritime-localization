from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from tools.audit_parallel_assignment_expansion import (
    _compare_overlap,
    _compose_selected_modes,
    _exact_rows,
    _rank_evaluations,
    _replay_composite_events,
    validate_serial_audit,
    validate_serial_audit_sources,
)


def _hypothesis(east: float, score: float) -> Hypothesis:
    return Hypothesis(
        east_m=east,
        north_m=0.0,
        score=score,
        assignments=tuple(
            Assignment(report_index=i, catalog_key=f"k{i}", residual_deg=0.0, cost=0.0)
            for i in range(5)
        ),
    )


def _hyp_json(hyp: Hypothesis) -> dict:
    return {
        "east_m": hyp.east_m,
        "north_m": hyp.north_m,
        "score": hyp.score,
        "assignments": [
            {
                "report_index": a.report_index,
                "catalog_key": a.catalog_key,
                "residual_deg": a.residual_deg,
                "cost": a.cost,
            }
            for a in hyp.assignments
        ],
        "score_components": None,
        "uncertainty": None,
    }


@dataclass
class FakeScorer:
    def evaluate(self, position: tuple[float, float]) -> object:
        east, _ = position
        score = 1.0 if east == 0.0 else 2.0
        return SimpleNamespace(status="scored", hypothesis=_hypothesis(east, score))


def _source_fixture() -> tuple[list[dict], list[dict], list[dict]]:
    rows = []
    assigned = []
    a20 = []
    for i in range(64):
        h0, h1 = _hypothesis(0.0, 1.0), _hypothesis(1.0, 2.0)
        alternatives = [
            {
                "label": "cached_hypothesis",
                "saved_hypothesis": _hyp_json(h0),
                "canonical_hypothesis": _hyp_json(h0),
            },
            {
                "label": "refined_mode.hypothesis",
                "saved_hypothesis": _hyp_json(h1),
                "canonical_hypothesis": _hyp_json(h1),
            },
        ]
        rows.append(
            {
                "mode_index": i,
                "input_index": i,
                "candidate_id": f"c{i}",
                "selected_start_label": "cached_hypothesis",
                "start_alternatives": alternatives,
                "seed": _hyp_json(h0),
                "best_hypothesis": _hyp_json(h0),
            }
        )
        assigned.append(
            {
                "input_index": i,
                "candidate_id": f"c{i}",
                "starts": [
                    {"label": "cached_hypothesis", "final_hypothesis": _hyp_json(h0)},
                    {"label": "refined_mode.hypothesis", "final_hypothesis": _hyp_json(h1)},
                ],
            }
        )
        a20.append({"input_index": i, "candidate_id": f"c{i}"})
    return rows, assigned, a20


def test_all_selected_modes_revalidate_score_minimum_starts_and_best() -> None:
    rows, assigned, a20 = _source_fixture()
    checked = _exact_rows(rows, assigned, a20, FakeScorer(), {})
    assert len(checked) == 64
    assert checked[0]["selected_start_label"] == "cached_hypothesis"


def test_nonminimum_start_is_rejected() -> None:
    rows, assigned, a20 = _source_fixture()
    rows[7]["selected_start_label"] = "refined_mode.hypothesis"
    with pytest.raises(ValueError, match="nonminimum start"):
        _exact_rows(rows, assigned, a20, FakeScorer(), {})


def test_saved_best_must_match_exact_scorer() -> None:
    rows, assigned, a20 = _source_fixture()
    rows[2]["best_hypothesis"] = _hyp_json(_hypothesis(2.0, 1.5))
    with pytest.raises(ValueError, match="best hypothesis differs"):
        _exact_rows(rows, assigned, a20, FakeScorer(), {})


def _mode_rows(ids: list[int]) -> list[dict]:
    return [{"mode_index": i, "counters": {}, "candidate_branch_count": 0} for i in ids]


def _event(mode: int, phase: str, branch_id: int, report: int = 0) -> dict:
    return {
        "mode_index": mode,
        "phase": phase,
        "event": {"branch_id": branch_id, "report_index": report, "catalog_key": f"k{report}"},
    }


def test_parallel_only_mode_pose_is_rescored_even_if_serial_audit_is_complete() -> None:
    h = _hypothesis(0.0, 1.0)
    rows = []
    events_by_mode = {i: [] for i in range(64)}
    for i in range(64):
        row: dict = {
            "seed": _hyp_json(h),
            "best_hypothesis": _hyp_json(h),
            "counters": {},
            "status": "no_unmatched_reports",
        }
        if i == 63:
            row["counters"] = {"visited_branches": 1, "unvisited_branches": 0}
            row["status"] = "completed"
            row["completed"] = True
            row["budget_exhausted"] = False
            events_by_mode[i] = [
                _event(i, "branch_started", 0),
                {
                    **_event(i, "branch_result", 0),
                    "event": {
                        "branch_id": 0,
                        "status": "no_improvement",
                        "exact_score": 1.0,
                        "proposed_pose": [0.0, 0.0],
                        "report_index": 0,
                        "catalog_key": "k0",
                        "added_assignment_retained": True,
                    },
                },
            ]
        row["_source_run"] = "parallel" if i == 63 else "serial"
        rows.append(row)
    # A parallel-selected row may reuse its exact serial overlap audit for modes 0..59.
    rows[10]["counters"] = {"visited_branches": 1, "unvisited_branches": 0}
    rows[10]["status"] = "completed"
    rows[10]["completed"] = True
    rows[10]["budget_exhausted"] = False
    events_by_mode[10] = [
        _event(10, "branch_started", 0),
        {
            **_event(10, "branch_result", 0),
            "event": {
                "branch_id": 0,
                "status": "no_improvement",
                "exact_score": 1.0,
                "proposed_pose": [0.0, 0.0],
                "report_index": 0,
                "catalog_key": "k0",
                "added_assignment_retained": True,
            },
        },
    ]
    result = _replay_composite_events(
        rows,
        events_by_mode,
        FakeScorer(),
        {},
        validated_serial_audit_modes=set(range(60)),
    )
    assert result["parallel_new_branch_results_rescored"] == 1
    assert result["serial_audited_branch_results_reused"] == 1


def test_parallel_only_branch_without_exact_pose_is_rejected() -> None:
    h = _hypothesis(0.0, 1.0)
    rows = []
    events_by_mode = {i: [] for i in range(64)}
    for i in range(64):
        row: dict = {
            "seed": _hyp_json(h),
            "best_hypothesis": _hyp_json(h),
            "counters": {},
            "status": "no_unmatched_reports",
        }
        row["_source_run"] = "serial"
        rows.append(row)
    rows[62].update(
        {
            "_source_run": "parallel",
            "counters": {"visited_branches": 1, "unvisited_branches": 0},
            "status": "completed",
            "completed": True,
            "budget_exhausted": False,
        }
    )
    events_by_mode[62] = [
        _event(62, "branch_started", 0),
        {
            "mode_index": 62,
            "phase": "branch_result",
            "event": {"branch_id": 0, "status": "no_improvement", "exact_score": 1.0},
        },
    ]
    with pytest.raises(ValueError, match="pose invalid"):
        _replay_composite_events(
            rows,
            events_by_mode,
            FakeScorer(),
            {},
            validated_serial_audit_modes=set(range(60)),
        )


def test_overlap_rejects_mutated_branch_payload() -> None:
    serial_rows = _mode_rows(list(range(60)))
    parallel_rows = _mode_rows([*range(48), *range(49, 64)])
    serial_events = [_event(10, "branch_started", 0), _event(10, "branch_result", 0)]
    parallel_events = [_event(10, "branch_started", 0), _event(10, "branch_result", 0, report=1)]
    with pytest.raises(ValueError, match="payload differs"):
        _compare_overlap(serial_rows, serial_events, parallel_rows, parallel_events)


def test_serial_audit_must_bind_full_event_hash_and_60_rows(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}")
    manifest = {"rows_sha256": "r", "events_sha256": "e", "event_count": 0, "event_digest": ""}
    rows = _mode_rows(list(range(60)))
    audit = {
        "schema": "a35-assignment-expansion-evaluation-v1",
        "evaluation_status": "complete_audit",
        "branch_rescore_complete": True,
        "evaluation_only": True,
        "truth_used_for_search_or_proposal_selection": False,
        "certificate_scope": "none",
        "full_domain_complete": False,
        "mode_count": 64,
        "completed_mode_count": 60,
        "pending_mode_count": 4,
        "expansion_status": "interrupted",
        "expansion_manifest_sha256": __import__("hashlib").sha256(b"{}").hexdigest(),
        "expansion_rows_sha256": "r",
        "expansion_events_sha256": "e",
        "expansion_event_count": 0,
        "expansion_event_digest": "",
        "mode_evaluations": [{"mode_index": i} for i in range(60)],
        "event_mode_counts": {str(i): {"started": 0, "result": 0, "failure": 0} for i in range(61)},
    }
    validate_serial_audit(audit, tmp_path, manifest, rows, [])
    audit["expansion_event_digest"] = "tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_serial_audit(audit, tmp_path, manifest, rows, [])


def test_composite_uses_serial_mode_48_and_parallel_for_other_modes() -> None:
    serial_rows = _mode_rows(list(range(60)))
    parallel_rows = _mode_rows([*range(48), *range(49, 64)])
    composite, sources, events = _compose_selected_modes(serial_rows, [], parallel_rows, [])
    assert len(composite) == 64
    assert composite[48]["_source_run"] == "serial"
    assert sources[48] == "serial"
    assert sources[47] == "parallel"
    assert sources[63] == "parallel"
    assert set(events) == set(range(64))


def test_composite_rejects_missing_additional_mode() -> None:
    serial_rows = _mode_rows(list(range(60)))
    parallel_rows = _mode_rows([*range(48), *range(49, 63)])
    with pytest.raises(ValueError, match="expected 63-mode inventory"):
        _compose_selected_modes(serial_rows, [], parallel_rows, [])


def test_serial_audit_upstream_source_hashes_are_required() -> None:
    bindings = {
        "run_identity_sha256": "run",
        "assigned_manifest_sha256": "assigned",
        "assigned_rows_sha256": "assigned-rows",
        "a20_manifest_sha256": "a20",
        "a20_rows_sha256": "a20-rows",
    }
    audit = {
        "source_run_identity_sha256": "run",
        "assigned_manifest_sha256": "assigned",
        "assigned_rows_sha256": "assigned-rows",
        "a20_manifest_sha256": "a20",
        "a20_rows_sha256": "a20-rows",
    }
    validate_serial_audit_sources(
        audit,
        run_identity_sha256=bindings["run_identity_sha256"],
        assigned_manifest_sha256=bindings["assigned_manifest_sha256"],
        assigned_rows_sha256=bindings["assigned_rows_sha256"],
        a20_manifest_sha256=bindings["a20_manifest_sha256"],
        a20_rows_sha256=bindings["a20_rows_sha256"],
    )
    audit["a20_rows_sha256"] = "mutated"
    with pytest.raises(ValueError, match="source binding mismatch"):
        validate_serial_audit_sources(
            audit,
            run_identity_sha256=bindings["run_identity_sha256"],
            assigned_manifest_sha256=bindings["assigned_manifest_sha256"],
            assigned_rows_sha256=bindings["assigned_rows_sha256"],
            a20_manifest_sha256=bindings["a20_manifest_sha256"],
            a20_rows_sha256=bindings["a20_rows_sha256"],
        )


@dataclass
class ImprovingFakeScorer:
    def evaluate(self, position: tuple[float, float]) -> object:
        east, _ = position
        score = 2.0 if east == 0.0 else 1.0
        return SimpleNamespace(status="scored", hypothesis=_hypothesis(east, score))


def test_final_best_must_match_strict_improving_winning_branch() -> None:
    rows = []
    events_by_mode = {i: [] for i in range(64)}
    for i in range(64):
        seed = _hypothesis(0.0, 2.0)
        best = _hypothesis(1.0, 1.0) if i == 0 else seed
        row: dict = {
            "seed": _hyp_json(seed),
            "best_hypothesis": _hyp_json(best),
            "counters": {},
            "status": "no_unmatched_reports",
        }
        row["_source_run"] = "serial"
        rows.append(row)
    rows[0].update(
        {
            "counters": {"visited_branches": 1, "unvisited_branches": 0},
            "status": "completed",
            "completed": True,
            "budget_exhausted": False,
        }
    )
    events_by_mode[0] = [
        _event(0, "branch_started", 0),
        {
            "mode_index": 0,
            "phase": "branch_result",
            "event": {
                "branch_id": 0,
                "status": "improved",
                "exact_score": 1.0,
                "proposed_pose": [1.0, 0.0],
                "report_index": 3,
                "catalog_key": "k3",
                "added_assignment_retained": True,
            },
        },
    ]
    _replay_composite_events(
        rows,
        events_by_mode,
        ImprovingFakeScorer(),
        {},
        validated_serial_audit_modes=set(range(60)),
    )
    rows[0]["best_hypothesis"] = _hyp_json(_hypothesis(2.0, 1.0))
    with pytest.raises(ValueError, match="final best pose differs"):
        _replay_composite_events(
            rows,
            events_by_mode,
            ImprovingFakeScorer(),
            {},
            validated_serial_audit_modes=set(range(60)),
        )


def test_score_rank_best_error_is_not_nearest_error() -> None:
    metrics = [
        {"mode_index": 0, "expanded": {"score": 1.0, "geodesic_error_m": 500.0}},
        {"mode_index": 1, "expanded": {"score": 2.0, "geodesic_error_m": 5.0}},
    ]
    score_rank, error_rank = _rank_evaluations(metrics)
    assert score_rank[0]["mode_index"] == 0
    assert error_rank[0]["mode_index"] == 1
    assert score_rank[0]["expanded"]["geodesic_error_m"] == 500.0
    assert error_rank[0]["expanded"]["geodesic_error_m"] == 5.0


def test_mode_60_is_rescored_even_if_source_label_says_serial() -> None:
    hypothesis = _hypothesis(0.0, 1.0)
    rows: list[dict[str, Any]] = []
    events_by_mode = {i: [] for i in range(64)}
    for i in range(64):
        rows.append(
            {
                "seed": _hyp_json(hypothesis),
                "best_hypothesis": _hyp_json(hypothesis),
                "counters": {},
                "status": "no_unmatched_reports",
                "_source_run": "serial",
            }
        )
    rows[60].update(
        {
            "counters": {"visited_branches": 1, "unvisited_branches": 0},
            "status": "completed",
            "completed": True,
            "budget_exhausted": False,
        }
    )
    events_by_mode[60] = [
        _event(60, "branch_started", 0),
        {
            "mode_index": 60,
            "phase": "branch_result",
            "event": {
                "branch_id": 0,
                "status": "no_improvement",
                "exact_score": 1.0,
                "proposed_pose": [0.0, 0.0],
                "report_index": 0,
                "catalog_key": "k0",
                "added_assignment_retained": True,
            },
        },
    ]
    result = _replay_composite_events(
        rows,
        events_by_mode,
        FakeScorer(),
        {},
        validated_serial_audit_modes=set(range(60)),
    )
    assert result["parallel_new_branch_results_rescored"] == 1
    assert result["serial_audited_branch_results_reused"] == 0


def test_final_best_assignment_must_include_winning_branch_edge() -> None:
    seed = _hypothesis(0.0, 2.0)
    best = Hypothesis(
        east_m=1.0,
        north_m=0.0,
        score=1.0,
        assignments=tuple(
            Assignment(report_index=i, catalog_key=f"bad{i}", residual_deg=0.0, cost=0.0)
            for i in range(5)
        ),
    )
    rows: list[dict[str, Any]] = []
    events_by_mode = {i: [] for i in range(64)}
    for i in range(64):
        rows.append(
            {
                "seed": _hyp_json(seed),
                "best_hypothesis": _hyp_json(best if i == 0 else seed),
                "counters": {},
                "status": "no_unmatched_reports",
                "_source_run": "serial",
            }
        )
    rows[0].update(
        {
            "counters": {"visited_branches": 1, "unvisited_branches": 0},
            "status": "completed",
            "completed": True,
            "budget_exhausted": False,
        }
    )
    events_by_mode[0] = [
        _event(0, "branch_started", 0),
        {
            "mode_index": 0,
            "phase": "branch_result",
            "event": {
                "branch_id": 0,
                "status": "improved",
                "exact_score": 1.0,
                "proposed_pose": [1.0, 0.0],
                "report_index": 3,
                "catalog_key": "k3",
                "added_assignment_retained": True,
            },
        },
    ]
    with pytest.raises(ValueError, match="omits winning branch assignment"):
        _replay_composite_events(
            rows,
            events_by_mode,
            ImprovingFakeScorer(),
            {},
            validated_serial_audit_modes=set(range(60)),
        )
