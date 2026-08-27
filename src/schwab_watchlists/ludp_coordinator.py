from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mb_watchlist_coordinator.adapters.tos import (
    MaterializationOperation,
)
from mb_watchlist_coordinator.adapters.tos_runtime import (
    ToSExecutor,
    ToSReconciliationStepResult,
    run_tos_reconciliation_step,
)
from mb_watchlist_coordinator.coordinator import (
    WatchlistCoordinator,
)
from mb_watchlist_coordinator.models import (
    IntentType,
    ProducerIntent,
)
from mb_watchlist_coordinator.transactions import (
    MaterializationTransactionStatus,
)


EASTERN = ZoneInfo("America/New_York")

LUDP_PRODUCER_ID = "nasdaq-ludp"


def build_ludp_intent(
    symbols: Iterable[str],
    *,
    intent_id: str,
    created_at: datetime,
    session_date: date,
) -> ProducerIntent:
    """
    Convert newly detected Nasdaq volatility-halt symbols into one
    coordinator ENSURE_PRESENT intent.

    LUDP additions remain effective for the ET trading date and expire
    at midnight before the next ET calendar date.
    """

    normalized = frozenset(
        symbol.strip().upper()
        for symbol in symbols
        if symbol.strip()
    )

    if not normalized:
        raise ValueError(
            "LUDP intent requires at least one symbol."
        )

    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise ValueError(
            "created_at must be timezone-aware."
        )

    if (
        created_at.astimezone(EASTERN).date()
        != session_date
    ):
        raise ValueError(
            "created_at must belong to session_date in ET."
        )

    expires_at = datetime.combine(
        session_date + timedelta(days=1),
        time.min,
        tzinfo=EASTERN,
    )

    return ProducerIntent(
        intent_id=intent_id,
        producer_id=LUDP_PRODUCER_ID,
        intent_type=IntentType.ENSURE_PRESENT,
        symbols=normalized,
        created_at=created_at,
        expires_at=expires_at,
        reason="Nasdaq volatility halt (LUDP/M)",
        metadata={
            "session_date": session_date.isoformat(),
            "reason_codes": ("LUDP", "M"),
        },
    )


def reconcile_tos_until_stable(
    coordinator: WatchlistCoordinator,
    executor: ToSExecutor,
    *,
    transaction_id_factory: Callable[[], str],
    now_factory: Callable[[], datetime],
    max_steps: int = 4,
) -> tuple[ToSReconciliationStepResult, ...]:
    """
    Reconcile ToS until its planner reaches NO_OP.

    OBSERVE and successful ADD/REPLACE operations may be followed by
    another step. A failed or outcome-unknown materialization stops
    immediately instead of blindly repeating the mutation.
    """

    if max_steps <= 0:
        raise ValueError(
            "max_steps must be positive."
        )

    steps: list[
        ToSReconciliationStepResult
    ] = []

    for _ in range(max_steps):
        step = run_tos_reconciliation_step(
            coordinator,
            executor,
            at=now_factory(),
            transaction_id_factory=(
                transaction_id_factory
            ),
        )

        steps.append(step)

        if (
            step.plan.operation
            is MaterializationOperation.NO_OP
        ):
            return tuple(steps)

        if step.transaction is not None:
            if (
                step.transaction.status
                is not MaterializationTransactionStatus.SUCCESS
            ):
                reason = (
                    step.transaction.failure_reason
                    or step.transaction.status.value
                )

                raise RuntimeError(
                    "ToS reconciliation did not "
                    "complete successfully: "
                    f"{reason}"
                )

    raise RuntimeError(
        "ToS reconciliation did not reach NO_OP "
        f"within {max_steps} steps."
    )
