from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import schwab_watchlists.ludp_coordinator as module

from mb_watchlist_coordinator.adapters.tos import (
    MaterializationOperation,
    ToSMaterializationPlan,
)
from mb_watchlist_coordinator.adapters.tos_runtime import (
    ToSReconciliationStepResult,
)
from mb_watchlist_coordinator.models import (
    IntentType,
)
from mb_watchlist_coordinator.transactions import (
    MaterializationTransactionStatus,
)

from schwab_watchlists.ludp_coordinator import (
    build_ludp_intent,
    reconcile_tos_until_stable,
)


EASTERN = ZoneInfo("America/New_York")


def make_plan(
    operation: MaterializationOperation,
) -> ToSMaterializationPlan:
    return ToSMaterializationPlan(
        adapter_id="tos",
        canonical_revision=1,
        operation=operation,
        target_symbols=frozenset({"AAPL", "TEMC"}),
        operation_symbols=(
            frozenset({"TEMC"})
            if operation
            is MaterializationOperation.ADD
            else frozenset()
        ),
    )


def test_build_ludp_intent_normalizes_symbols_and_expires_at_et_midnight():
    created_at = datetime(
        2026,
        8,
        27,
        10,
        15,
        tzinfo=EASTERN,
    )

    intent = build_ludp_intent(
        [
            " temc ",
            "TEMC",
            "abcd",
        ],
        intent_id="ludp-001",
        created_at=created_at,
        session_date=date(
            2026,
            8,
            27,
        ),
    )

    assert intent.intent_id == "ludp-001"
    assert intent.producer_id == "nasdaq-ludp"

    assert (
        intent.intent_type
        is IntentType.ENSURE_PRESENT
    )

    assert intent.symbols == frozenset(
        {
            "TEMC",
            "ABCD",
        }
    )

    assert intent.expires_at == datetime(
        2026,
        8,
        28,
        0,
        0,
        tzinfo=EASTERN,
    )

    assert intent.metadata[
        "reason_codes"
    ] == (
        "LUDP",
        "M",
    )


def test_build_ludp_intent_rejects_empty_symbols():
    with pytest.raises(
        ValueError,
        match="at least one symbol",
    ):
        build_ludp_intent(
            [],
            intent_id="ludp-empty",
            created_at=datetime(
                2026,
                8,
                27,
                10,
                15,
                tzinfo=EASTERN,
            ),
            session_date=date(
                2026,
                8,
                27,
            ),
        )


def test_build_ludp_intent_requires_matching_et_session_date():
    with pytest.raises(
        ValueError,
        match="session_date",
    ):
        build_ludp_intent(
            ["TEMC"],
            intent_id="ludp-wrong-date",
            created_at=datetime(
                2026,
                8,
                28,
                0,
                5,
                tzinfo=EASTERN,
            ),
            session_date=date(
                2026,
                8,
                27,
            ),
        )


def test_reconcile_tos_until_stable_runs_observe_add_noop(
    monkeypatch,
):
    operations = [
        MaterializationOperation.OBSERVE,
        MaterializationOperation.ADD,
        MaterializationOperation.NO_OP,
    ]

    calls = []

    def fake_step(
        coordinator,
        executor,
        *,
        at,
        transaction_id_factory,
    ):
        operation = operations[
            len(calls)
        ]

        calls.append(operation)

        transaction = None

        if (
            operation
            is MaterializationOperation.ADD
        ):
            transaction = SimpleNamespace(
                status=(
                    MaterializationTransactionStatus.SUCCESS
                ),
                failure_reason=None,
            )

        return ToSReconciliationStepResult(
            plan=make_plan(
                operation
            ),
            transaction=transaction,
        )

    monkeypatch.setattr(
        module,
        "run_tos_reconciliation_step",
        fake_step,
    )

    now = datetime(
        2026,
        8,
        27,
        10,
        15,
        tzinfo=EASTERN,
    )

    steps = reconcile_tos_until_stable(
        SimpleNamespace(),
        SimpleNamespace(),
        transaction_id_factory=lambda: "T001",
        now_factory=lambda: now,
    )

    assert [
        step.plan.operation
        for step in steps
    ] == [
        MaterializationOperation.OBSERVE,
        MaterializationOperation.ADD,
        MaterializationOperation.NO_OP,
    ]

    assert len(calls) == 3


def test_reconcile_tos_until_stable_stops_after_failed_materialization(
    monkeypatch,
):
    calls = []

    def fake_step(
        coordinator,
        executor,
        *,
        at,
        transaction_id_factory,
    ):
        calls.append("called")

        return ToSReconciliationStepResult(
            plan=make_plan(
                MaterializationOperation.ADD
            ),
            transaction=SimpleNamespace(
                status=(
                    MaterializationTransactionStatus
                    .INTERRUPTED_OUTCOME_UNKNOWN
                ),
                failure_reason=(
                    "verification evidence unavailable"
                ),
            ),
        )

    monkeypatch.setattr(
        module,
        "run_tos_reconciliation_step",
        fake_step,
    )

    now = datetime(
        2026,
        8,
        27,
        10,
        15,
        tzinfo=EASTERN,
    )

    with pytest.raises(
        RuntimeError,
        match="verification evidence unavailable",
    ):
        reconcile_tos_until_stable(
            SimpleNamespace(),
            SimpleNamespace(),
            transaction_id_factory=lambda: "T001",
            now_factory=lambda: now,
        )

    # Crucial: no automatic second mutation attempt.
    assert len(calls) == 1
