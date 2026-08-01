import pytest

from candidate_filters import (
    FilterSettings,
    MissingFieldPolicy,
    filter_candidates,
)
from candidate_model import MarketSession, SymbolCandidate


def make_candidate(
    symbol: str,
    *,
    price: float | None = 10.0,
    volume: int | None = 1_000_000,
    percent_change: float | None = 5.0,
) -> SymbolCandidate:
    return SymbolCandidate(
        symbol=symbol,
        source="test",
        session=MarketSession.REGULAR,
        last_price=price,
        volume=volume,
        percent_change=percent_change,
    )


def test_candidate_normalizes_symbol() -> None:
    candidate = make_candidate(" nvda ")

    assert candidate.symbol == "NVDA"


def test_candidate_passes_all_filters() -> None:
    settings = FilterSettings(
        min_price=1.0,
        max_price=20.0,
        min_volume=500_000,
        min_percent_change=2.0,
        max_percent_change=10.0,
    )

    result = filter_candidates(
        [make_candidate("AAPL")],
        settings,
    )

    assert [candidate.symbol for candidate in result.accepted] == ["AAPL"]
    assert result.rejected == ()


def test_candidate_can_have_multiple_rejection_reasons() -> None:
    settings = FilterSettings(
        min_price=1.0,
        min_volume=1_000_000,
        min_percent_change=5.0,
    )

    result = filter_candidates(
        [
            make_candidate(
                "LOW",
                price=0.25,
                volume=10_000,
                percent_change=-2.0,
            )
        ],
        settings,
    )

    decision = result.rejected[0]

    assert not decision.accepted
    assert len(decision.reasons) == 3
    assert "price" in decision.reasons[0]
    assert "volume" in decision.reasons[1]
    assert "change" in decision.reasons[2]


def test_missing_required_field_rejects_by_default() -> None:
    settings = FilterSettings(min_volume=100_000)

    result = filter_candidates(
        [make_candidate("NOVOL", volume=None)],
        settings,
    )

    assert result.accepted == ()
    assert "volume is missing" in result.rejected[0].reasons[0]


def test_missing_required_field_can_be_allowed() -> None:
    settings = FilterSettings(
        min_volume=100_000,
        missing_field_policy=MissingFieldPolicy.ALLOW,
    )

    result = filter_candidates(
        [make_candidate("NOVOL", volume=None)],
        settings,
    )

    assert [candidate.symbol for candidate in result.accepted] == ["NOVOL"]


def test_percent_change_uses_percentage_points() -> None:
    settings = FilterSettings(min_percent_change=5.0)

    result = filter_candidates(
        [
            make_candidate("PASS", percent_change=5.1),
            make_candidate("FAIL", percent_change=0.051),
        ],
        settings,
    )

    assert [candidate.symbol for candidate in result.accepted] == ["PASS"]
    assert [decision.candidate.symbol for decision in result.rejected] == [
        "FAIL"
    ]


def test_result_limit_preserves_input_order() -> None:
    settings = FilterSettings(max_results=2)

    result = filter_candidates(
        [
            make_candidate("AAA"),
            make_candidate("BBB"),
            make_candidate("CCC"),
        ],
        settings,
    )

    assert [candidate.symbol for candidate in result.accepted] == [
        "AAA",
        "BBB",
    ]

    assert result.rejected[0].candidate.symbol == "CCC"
    assert result.rejected[0].reasons == ("result limit 2 reached",)


def test_invalid_price_range_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="min_price cannot exceed max_price",
    ):
        FilterSettings(
            min_price=20.0,
            max_price=10.0,
        )
