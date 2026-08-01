from datetime import datetime, timezone

import pytest

from wl_schwab_movers import mover_record_to_candidate


def test_mover_record_converts_to_candidate() -> None:
    record = {
        "symbol": "nvda",
        "description": "NVIDIA CORP",
        "lastPrice": 199.02,
        "netPercentChange": 0.0204,
        "volume": 139_667_397,
        "trades": 500_000,
        "marketShare": 2.5,
    }

    as_of = datetime(
        2026,
        7,
        31,
        20,
        0,
        tzinfo=timezone.utc,
    )

    candidate = mover_record_to_candidate(
        record,
        source_rank=3,
        as_of=as_of,
    )

    assert candidate.symbol == "NVDA"
    assert candidate.source == "schwab_movers"
    assert candidate.as_of == as_of
    assert candidate.session is None

    assert candidate.last_price == pytest.approx(199.02)
    assert candidate.percent_change == pytest.approx(2.04)

    assert candidate.volume == 139_667_397
    assert candidate.trades == 500_000
    assert candidate.market_share_percent == pytest.approx(2.5)

    assert candidate.description == "NVIDIA CORP"
    assert candidate.source_rank == 3
    assert candidate.raw["netPercentChange"] == pytest.approx(0.0204)


def test_mover_record_allows_missing_optional_fields() -> None:
    candidate = mover_record_to_candidate(
        {"symbol": "TEST"},
        source_rank=1,
        as_of=datetime.now(timezone.utc),
    )

    assert candidate.symbol == "TEST"
    assert candidate.last_price is None
    assert candidate.percent_change is None
    assert candidate.volume is None


def test_mover_record_requires_symbol() -> None:
    with pytest.raises(
        ValueError,
        match="does not contain a symbol",
    ):
        mover_record_to_candidate(
            {"lastPrice": 10.0},
            source_rank=1,
            as_of=datetime.now(timezone.utc),
        )
