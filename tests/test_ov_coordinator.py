from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mb_watchlist_coordinator.models import (
    IntentType,
)

from schwab_watchlists.ov_coordinator import (
    OV_PRODUCER_ID,
    build_ov_base_intent,
    select_ov_symbols,
)


EASTERN = ZoneInfo("America/New_York")


def snapshot(
    symbol: str,
    ov_decision: int | None,
    *,
    usable_ov: bool = True,
    schwab_quote: bool = True,
):
    return SimpleNamespace(
        symbol=symbol,
        ov_decision=ov_decision,
        has_usable_ov_decision=usable_ov,
        has_schwab_quote=schwab_quote,
    )


def batch(
    *snapshots,
    trade_date=date(2026, 8, 27),
):
    return SimpleNamespace(
        trade_date=trade_date,
        snapshots=tuple(snapshots),
    )


def test_select_ov_symbols_ranks_by_volume_descending():
    source = batch(
        snapshot("BBBB", 500),
        snapshot("AAAA", 500),
        snapshot("CCCC", 900),
        snapshot("DDDD", 100),
    )

    result = select_ov_symbols(
        source,
        limit=3,
    )

    assert result.selected_symbols == (
        "CCCC",
        "AAAA",
        "BBBB",
    )

    assert result.eligible_count == 4


def test_select_ov_symbols_excludes_unusable_sources():
    source = batch(
        snapshot("GOOD", 900),
        snapshot(
            "BADOV",
            1000,
            usable_ov=False,
        ),
        snapshot(
            "BADQUOTE",
            1100,
            schwab_quote=False,
        ),
        snapshot(
            "NONE",
            None,
        ),
    )

    result = select_ov_symbols(
        source,
        limit=10,
    )

    assert result.selected_symbols == (
        "GOOD",
    )

    assert result.excluded_symbols == (
        "BADOV",
        "BADQUOTE",
        "NONE",
    )


def test_build_ov_base_intent_creates_daily_base_set():
    source = batch(
        snapshot("NVDA", 900),
        snapshot("AAPL", 700),
        snapshot("MSFT", 500),
    )

    created_at = datetime(
        2026,
        8,
        27,
        9,
        25,
        tzinfo=EASTERN,
    )

    intent = build_ov_base_intent(
        source,
        limit=2,
        intent_id="ov-001",
        created_at=created_at,
    )

    assert intent.intent_id == "ov-001"

    assert (
        intent.producer_id
        == OV_PRODUCER_ID
    )

    assert (
        intent.intent_type
        is IntentType.BASE_SET
    )

    assert intent.symbols == frozenset(
        {
            "NVDA",
            "AAPL",
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
        "ranked_symbols"
    ] == (
        "NVDA",
        "AAPL",
    )


def test_build_ov_base_intent_rejects_empty_selection():
    source = batch(
        snapshot(
            "BAD",
            None,
            usable_ov=False,
        ),
    )

    with pytest.raises(
        ValueError,
        match="no eligible symbols",
    ):
        build_ov_base_intent(
            source,
            limit=10,
            intent_id="ov-empty",
            created_at=datetime(
                2026,
                8,
                27,
                9,
                25,
                tzinfo=EASTERN,
            ),
        )


def test_build_ov_base_intent_requires_matching_trade_date():
    source = batch(
        snapshot("NVDA", 900),
        trade_date=date(
            2026,
            8,
            27,
        ),
    )

    with pytest.raises(
        ValueError,
        match="trade date",
    ):
        build_ov_base_intent(
            source,
            limit=10,
            intent_id="ov-wrong-date",
            created_at=datetime(
                2026,
                8,
                28,
                9,
                25,
                tzinfo=EASTERN,
            ),
        )
