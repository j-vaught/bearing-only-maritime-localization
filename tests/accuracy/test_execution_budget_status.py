"""Interruption reasons preserve honest candidate counts and old result payloads."""

from __future__ import annotations

from typing import cast

import pytest

from maritime.schemas.localization import (
    BudgetExhaustionReason,
    Hypothesis,
    LocalizationResult,
    SearchStatus,
)
from maritime.serialization import (
    contract_from_dict,
    contract_to_dict,
    dumps_contract,
    loads_contract,
)


@pytest.mark.parametrize("reason", ["request_deadline", "assignment_branch_limit"])
@pytest.mark.parametrize("used", [0, 4, 100])
def test_non_candidate_limit_keeps_actual_candidate_counts(reason: str, used: int) -> None:
    value = LocalizationResult(
        (Hypothesis(1.0, 2.0, 3.0),),
        2.0,
        "ambiguous",
        "budget_exhausted",
        budget_limit=100,
        budget_used=used,
        budget_exhaustion_reason=cast(BudgetExhaustionReason, reason),
    )
    assert value.budget_used == used
    assert loads_contract(dumps_contract(value), LocalizationResult) == value
    assert value.accepted_index is None
    assert value.certificate_scope == "none"


def test_request_deadline_before_candidate_plan_needs_no_fake_budget() -> None:
    value = LocalizationResult(
        (), 1.0, "no_fix", "budget_exhausted", budget_exhaustion_reason="request_deadline"
    )
    assert value.budget_used is None and value.budget_limit is None
    assert loads_contract(dumps_contract(value), LocalizationResult) == value


@pytest.mark.parametrize("reason", [None, "candidate_limit"])
def test_legacy_candidate_budget_rule_remains_strict(reason: BudgetExhaustionReason | None) -> None:
    with pytest.raises(ValueError, match="fully consumed"):
        LocalizationResult(
            (),
            1.0,
            "no_fix",
            "budget_exhausted",
            budget_limit=100,
            budget_used=4,
            budget_exhaustion_reason=reason,
        )
    value = LocalizationResult(
        (),
        1.0,
        "no_fix",
        "budget_exhausted",
        budget_limit=100,
        budget_used=100,
        budget_exhaustion_reason=reason,
    )
    assert value.budget_used == value.budget_limit


def test_old_result_json_omits_new_optional_reason_and_remains_readable() -> None:
    value = LocalizationResult((), 1.0, "no_fix", "budget_exhausted", budget_limit=2, budget_used=2)
    payload = contract_to_dict(value)
    assert "budget_exhaustion_reason" not in payload
    assert contract_from_dict(LocalizationResult, payload) == value
    payload["budget_exhaustion_reason"] = None
    assert contract_from_dict(LocalizationResult, payload) == value
    payload["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected"):
        contract_from_dict(LocalizationResult, payload)


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_reason_cannot_hide_an_interruption_in_another_status(status: str) -> None:
    with pytest.raises(ValueError, match="requires budget_exhausted"):
        LocalizationResult(
            (),
            1.0,
            "no_fix",
            cast(SearchStatus, status),
            budget_exhaustion_reason="request_deadline",
        )


def test_interrupted_non_candidate_budget_cannot_accept_or_certify() -> None:
    with pytest.raises(ValueError, match="unless the search completed"):
        LocalizationResult(
            (Hypothesis(1.0, 2.0, 3.0),),
            1.0,
            "accepted",
            "budget_exhausted",
            accepted_index=0,
            budget_exhaustion_reason="request_deadline",
        )
    with pytest.raises(ValueError, match="no certificate"):
        LocalizationResult(
            (),
            1.0,
            "no_fix",
            "budget_exhausted",
            certificate_scope="finite_candidate_set",
            budget_exhaustion_reason="assignment_branch_limit",
        )


def test_reason_does_not_bypass_counter_validation() -> None:
    with pytest.raises(ValueError, match="within budget_limit"):
        LocalizationResult(
            (),
            1.0,
            "no_fix",
            "budget_exhausted",
            budget_limit=2,
            budget_used=3,
            budget_exhaustion_reason="request_deadline",
        )
    with pytest.raises(ValueError, match="Unsupported budget_exhaustion_reason"):
        LocalizationResult(
            (),
            1.0,
            "no_fix",
            "budget_exhausted",
            budget_exhaustion_reason=cast(BudgetExhaustionReason, "unknown"),
        )
