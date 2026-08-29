from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pathlib import Path
from typing import Any

from mb_market_data.decision_batch import (
    DecisionSnapshotBatch,
    build_decision_snapshot_batch,
)
from mb_market_data.schwab_quotes import (
    DEFAULT_QUOTE_BATCH_SIZE,
    fetch_quotes_batched,
)
from mb_market_data.tos_watchlist import (
    read_tos_watchlist,
)


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
class OVDecisionEvaluation:
    """
    Audit result for one symbol considered by the OV selector.

    ov_rank is the symbol's rank among all symbols having a usable
    OV_DECISION, independent of Schwab quote availability.

    eligible_rank is its rank among symbols that satisfy the current
    selection policy.
    """

    symbol: str
    ov_rank: int | None
    eligible_rank: int | None
    eligible: bool
    selected: bool
    exclusion_reason: str | None


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
    evaluations: tuple[OVDecisionEvaluation, ...]


UTC = timezone.utc


def acquire_live_ov_batch(
    client: Any,
    watchlist_path: Path,
    *,
    trade_date: date,
    fields: str = "all",
    batch_size: int = DEFAULT_QUOTE_BATCH_SIZE,
    observed_at_factory=lambda: datetime.now(UTC),
) -> DecisionSnapshotBatch:
    """
    Acquire the minimal live inputs needed for the OV POC.

    Sequence intentionally matches the proven mb_market_data live probe:

        read ToS OV_DECISION file
            ->
        record when MasterBot accepted that data
            ->
        fetch Schwab quotes for those symbols
            ->
        assemble DecisionSnapshotBatch
    """

    watchlist_path = Path(
        watchlist_path
    ).expanduser()

    watchlist = read_tos_watchlist(
        watchlist_path
    )

    tos_observed_at_utc = (
        observed_at_factory()
    )

    symbols = [
        row.symbol
        for row in watchlist.rows
    ]

    quote_batch = fetch_quotes_batched(
        client,
        symbols,
        fields=fields,
        batch_size=batch_size,
    )

    return build_decision_snapshot_batch(
        trade_date=trade_date,
        watchlist=watchlist,
        quote_batch=quote_batch,
        tos_observed_at_utc=(
            tos_observed_at_utc
        ),
    )


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

    usable_ov = [
        snapshot
        for snapshot in batch.snapshots
        if (
            snapshot.has_usable_ov_decision
            and snapshot.ov_decision is not None
        )
    ]

    usable_ov.sort(
        key=lambda snapshot: (
            -snapshot.ov_decision,
            snapshot.symbol,
        )
    )

    ov_rank_by_symbol = {
        snapshot.symbol: rank
        for rank, snapshot in enumerate(
            usable_ov,
            start=1,
        )
    }

    eligible = [
        snapshot
        for snapshot in usable_ov
        if snapshot.has_schwab_quote
    ]

    eligible_rank_by_symbol = {
        snapshot.symbol: rank
        for rank, snapshot in enumerate(
            eligible,
            start=1,
        )
    }

    selected_symbols = tuple(
        snapshot.symbol
        for snapshot in eligible[:limit]
    )

    selected_set = set(
        selected_symbols
    )

    evaluations = []

    for snapshot in batch.snapshots:
        symbol = snapshot.symbol

        if symbol not in ov_rank_by_symbol:
            eligible_symbol = False
            exclusion_reason = (
                "ov_decision_unusable"
            )

        elif not snapshot.has_schwab_quote:
            eligible_symbol = False
            exclusion_reason = (
                "schwab_quote_unavailable"
            )

        else:
            eligible_symbol = True
            exclusion_reason = None

        evaluations.append(
            OVDecisionEvaluation(
                symbol=symbol,
                ov_rank=(
                    ov_rank_by_symbol.get(
                        symbol
                    )
                ),
                eligible_rank=(
                    eligible_rank_by_symbol.get(
                        symbol
                    )
                ),
                eligible=eligible_symbol,
                selected=(
                    symbol in selected_set
                ),
                exclusion_reason=(
                    exclusion_reason
                ),
            )
        )

    excluded_symbols = tuple(
        sorted(
            evaluation.symbol
            for evaluation in evaluations
            if not evaluation.eligible
        )
    )

    return OVSelection(
        selected_symbols=selected_symbols,
        eligible_count=len(eligible),
        excluded_symbols=excluded_symbols,
        evaluations=tuple(
            evaluations
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
    selection: OVSelection | None = None,
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

    if selection is None:
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
