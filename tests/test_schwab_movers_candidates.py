from datetime import datetime, timezone

import pytest

from schwab_movers_source import (
    fetch_schwab_movers,
    mover_record_to_candidate,
)


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


class FakeResponse:
    def __init__(
        self,
        data,
        *,
        status_code: int = 200,
        url: str = "https://example.test/movers",
    ) -> None:
        self._data = data
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []

    def movers(
        self,
        symbol: str,
        sort: str | None = None,
        frequency: int | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "symbol": symbol,
                "sort": sort,
                "frequency": frequency,
            }
        )
        return self.response


def test_fetch_schwab_movers_orders_and_converts_records() -> None:
    response = FakeResponse(
        {
            "screeners": [
                {
                    "symbol": "LOW",
                    "lastPrice": 2.0,
                    "netPercentChange": 0.05,
                    "volume": 100,
                },
                {
                    "symbol": "HIGH",
                    "lastPrice": 4.0,
                    "netPercentChange": 0.25,
                    "volume": 200,
                },
            ]
        }
    )

    client = FakeClient(response)

    batch = fetch_schwab_movers(
        client,
        market="NASDAQ",
        sort_name="PERCENT_CHANGE_UP",
        frequency=5,
    )

    assert client.calls == [
        {
            "symbol": "NASDAQ",
            "sort": "PERCENT_CHANGE_UP",
            "frequency": 5,
        }
    ]

    assert [candidate.symbol for candidate in batch.candidates] == [
        "HIGH",
        "LOW",
    ]

    assert batch.candidates[0].percent_change == pytest.approx(25.0)
    assert batch.candidates[1].percent_change == pytest.approx(5.0)

    assert [record["symbol"] for record in batch.records] == [
        "HIGH",
        "LOW",
    ]


def test_fetch_schwab_movers_rejects_non_dictionary_json() -> None:
    client = FakeClient(
        FakeResponse(
            [
                {"symbol": "TEST"},
            ]
        )
    )

    with pytest.raises(
        ValueError,
        match="top-level JSON value",
    ):
        fetch_schwab_movers(
            client,
            market="NASDAQ",
            sort_name="VOLUME",
            frequency=5,
        )
