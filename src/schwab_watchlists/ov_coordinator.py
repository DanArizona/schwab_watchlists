from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mb_market_data.decision_batch import (
    DecisionSnapshotBatch,
)
from mb_watchlist_coordinator.models import (
    IntentType,
    ProducerIntent,
)


EASTERN = ZoneInfo("America/New_York")

OV_PRODUCER_ID = "overnight-volume"


@dataclass(frozen=True, slots=True)
class OVSelection:
    """
    Minimal POC Overnight Volume selection result.

    Membership is ranked solely by current OV_DECISION.
    More sophisticated historical features belong later.
    """

    selected_symbols: tuple[str, ...]
    eligible_count: int
    excluded_symbols: tuple[str, ...]


def select_ov_symbols(
    batch: DecisionSnapshotBatch,
    *,
    limit: int,
) -> OVSelection:
    """
    Select the highest-OV symbols from one decision snapshot batch.

    For the POC, a symbol is eligible only when:
      - OV_DECISION is usable;
      - Schwab returned a quote;
      - OV_DECISION is present.

    Ranking:
      1. OV_DECISION descending
      2. symbol ascending for deterministic ties
    """

    if limit <= 0:
        raise ValueError(
            "OV selection limit must be positive."
        )

    eligible = []
    excluded = []

    for snapshot in batch.snapshots:
        if (
            snapshot.has_usable_ov_decision
            and snapshot.has_schwab_quote
            and snapshot.ov_decision is not None
        ):
            eligible.append(snapshot)
        else:
            excluded.append(snapshot.symbol)

    eligible.sort(
        key=lambda snapshot: (
            -snapshot.ov_decision,
            snapshot.symbol,
        )
    )

    selected = tuple(
        snapshot.symbol
        for snapshot in eligible[:limit]
    )

    return OVSelection(
        selected_symbols=selected,
        eligible_count=len(eligible),
        excluded_symbols=tuple(
            sorted(excluded)
        ),
    )


def _session_expiry(
    session_date: date,
) -> datetime:
    return datetime.combine(
        session_date + timedelta(days=1),
        time.min,
        tzinfo=EASTERN,
    )


def build_ov_base_intent(
    batch: DecisionSnapshotBatch,
    *,
    limit: int,
    intent_id: str,
    created_at: datetime,
) -> ProducerIntent:
    """
    Build the day's authoritative Overnight Volume BASE_SET.
    """

    if (
        created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise ValueError(
            "created_at must be timezone-aware."
        )

    created_date_et = (
        created_at
        .astimezone(EASTERN)
        .date()
    )

    if created_date_et != batch.trade_date:
        raise ValueError(
            "created_at must belong to the "
            "DecisionSnapshotBatch trade date in ET."
        )

    selection = select_ov_symbols(
        batch,
        limit=limit,
    )

    if not selection.selected_symbols:
        raise ValueError(
            "OV selection produced no eligible symbols."
        )

    return ProducerIntent(
        intent_id=intent_id,
        producer_id=OV_PRODUCER_ID,
        intent_type=IntentType.BASE_SET,
        symbols=frozenset(
            selection.selected_symbols
        ),
        created_at=created_at,
        expires_at=_session_expiry(
            batch.trade_date
        ),
        reason=(
            "Opening Watchlist selected by "
            "current Overnight Volume"
        ),
        metadata={
            "trade_date": (
                batch.trade_date.isoformat()
            ),
            "ranking_method": (
                "OV_DECISION_DESC"
            ),
            "requested_limit": limit,
            "eligible_count": (
                selection.eligible_count
            ),
            #
            # Preserve ranking for audit purposes even though
            # canonical Watchlist membership is set-based.
            #
            "ranked_symbols": (
                selection.selected_symbols
            ),
        },
    )
