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


def test_acquire_live_ov_batch_reads_tos_then_fetches_quotes(
    monkeypatch,
    tmp_path,
):
    import schwab_watchlists.ov_coordinator as module

    watchlist_path = (
        tmp_path
        / "2026-08-27-WL.csv"
    )

    fake_watchlist = SimpleNamespace(
        rows=(
            SimpleNamespace(
                symbol="NVDA"
            ),
            SimpleNamespace(
                symbol="AAPL"
            ),
        ),
    )

    fake_quote_batch = object()
    fake_decision_batch = object()

    events = []

    monkeypatch.setattr(
        module,
        "read_tos_watchlist",
        lambda path: (
            events.append(
                ("read", path)
            )
            or fake_watchlist
        ),
    )

    def fake_fetch(
        client,
        symbols,
        *,
        fields,
        batch_size,
    ):
        events.append(
            (
                "fetch",
                client,
                tuple(symbols),
                fields,
                batch_size,
            )
        )

        return fake_quote_batch

    monkeypatch.setattr(
        module,
        "fetch_quotes_batched",
        fake_fetch,
    )

    observed_at = datetime(
        2026,
        8,
        27,
        13,
        25,
        tzinfo=ZoneInfo("UTC"),
    )

    def fake_build(**kwargs):
        events.append(
            (
                "build",
                kwargs,
            )
        )

        return fake_decision_batch

    monkeypatch.setattr(
        module,
        "build_decision_snapshot_batch",
        fake_build,
    )

    client = object()

    result = module.acquire_live_ov_batch(
        client,
        watchlist_path,
        trade_date=date(
            2026,
            8,
            27,
        ),
        observed_at_factory=(
            lambda: observed_at
        ),
    )

    assert result is fake_decision_batch

    assert events[0] == (
        "read",
        watchlist_path,
    )

    assert events[1][0] == "fetch"

    assert events[1][2] == (
        "NVDA",
        "AAPL",
    )

    assert events[1][3] == "all"

    assert events[2][0] == "build"

    build_kwargs = events[2][1]

    assert build_kwargs[
        "trade_date"
    ] == date(
        2026,
        8,
        27,
    )

    assert build_kwargs[
        "watchlist"
    ] is fake_watchlist

    assert build_kwargs[
        "quote_batch"
    ] is fake_quote_batch

    assert build_kwargs[
        "tos_observed_at_utc"
    ] == observed_at


def test_acquire_live_ov_batch_preserves_requested_fields_and_batch_size(
    monkeypatch,
    tmp_path,
):
    import schwab_watchlists.ov_coordinator as module

    fake_watchlist = SimpleNamespace(
        rows=(
            SimpleNamespace(
                symbol="NVDA"
            ),
        ),
    )

    monkeypatch.setattr(
        module,
        "read_tos_watchlist",
        lambda path: fake_watchlist,
    )

    captured = {}

    def fake_fetch(
        client,
        symbols,
        *,
        fields,
        batch_size,
    ):
        captured["fields"] = fields
        captured["batch_size"] = (
            batch_size
        )

        return object()

    monkeypatch.setattr(
        module,
        "fetch_quotes_batched",
        fake_fetch,
    )

    expected = object()

    monkeypatch.setattr(
        module,
        "build_decision_snapshot_batch",
        lambda **kwargs: expected,
    )

    result = module.acquire_live_ov_batch(
        object(),
        tmp_path / "source.csv",
        trade_date=date(
            2026,
            8,
            27,
        ),
        fields="quote",
        batch_size=17,
    )

    assert result is expected

    assert captured == {
        "fields": "quote",
        "batch_size": 17,
    }
    

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


def test_select_ov_symbols_records_audit_evaluations():
    source = batch(
        snapshot("TOP", 1000),
        snapshot(
            "NOQUOTE",
            900,
            schwab_quote=False,
        ),
        snapshot("NEXT", 800),
        snapshot(
            "BADOV",
            None,
            usable_ov=False,
        ),
    )

    result = select_ov_symbols(
        source,
        limit=2,
    )

    by_symbol = {
        evaluation.symbol: evaluation
        for evaluation in result.evaluations
    }

    assert by_symbol["TOP"].ov_rank == 1
    assert by_symbol["TOP"].eligible_rank == 1
    assert by_symbol["TOP"].eligible is True
    assert by_symbol["TOP"].selected is True
    assert (
        by_symbol["TOP"].exclusion_reason
        is None
    )

    assert by_symbol["NOQUOTE"].ov_rank == 2
    assert (
        by_symbol["NOQUOTE"].eligible_rank
        is None
    )
    assert (
        by_symbol["NOQUOTE"].eligible
        is False
    )
    assert (
        by_symbol["NOQUOTE"].selected
        is False
    )
    assert (
        by_symbol["NOQUOTE"].exclusion_reason
        == "schwab_quote_unavailable"
    )

    assert by_symbol["NEXT"].ov_rank == 3
    assert by_symbol["NEXT"].eligible_rank == 2
    assert by_symbol["NEXT"].selected is True

    assert by_symbol["BADOV"].ov_rank is None
    assert (
        by_symbol["BADOV"].eligible_rank
        is None
    )
    assert (
        by_symbol["BADOV"].exclusion_reason
        == "ov_decision_unusable"
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


def test_build_ov_base_intent_accepts_precomputed_selection(
    monkeypatch,
):
    import schwab_watchlists.ov_coordinator as module

    source = batch(
        snapshot("FNGR", 1000),
        snapshot("CYAB", 900),
        snapshot("DUO", 800),
    )

    selection = select_ov_symbols(
        source,
        limit=2,
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "selection should not be recalculated"
        )

    monkeypatch.setattr(
        module,
        "select_ov_symbols",
        fail_if_called,
    )

    intent = module.build_ov_base_intent(
        source,
        limit=2,
        intent_id="ov-precomputed",
        created_at=datetime(
            2026,
            8,
            27,
            9,
            25,
            tzinfo=EASTERN,
        ),
        selection=selection,
    )

    assert intent.metadata[
        "ranked_symbols"
    ] == (
        "FNGR",
        "CYAB",
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
