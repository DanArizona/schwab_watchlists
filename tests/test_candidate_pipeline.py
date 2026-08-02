from datetime import datetime, timezone

import pytest

from candidate_filters import FilterSettings
from candidate_model import SymbolCandidate
from candidate_pipeline import run_candidate_pipeline


def make_candidate(
    symbol: str,
    *,
    price: float = 10.0,
    volume: int = 1_000_000,
    percent_change: float = 5.0,
) -> SymbolCandidate:
    return SymbolCandidate(
        symbol=symbol,
        source="test_source",
        last_price=price,
        volume=volume,
        percent_change=percent_change,
    )


def test_pipeline_reports_counts_and_symbols() -> None:
    settings = FilterSettings(
        min_price=1.0,
        min_percent_change=2.0,
    )

    result = run_candidate_pipeline(
        [
            make_candidate("PASS"),
            make_candidate(
                "LOW",
                price=0.25,
            ),
            make_candidate(
                "DOWN",
                percent_change=-5.0,
            ),
        ],
        settings,
        source_name="test_source",
    )

    assert result.input_count == 3
    assert result.accepted_count == 1
    assert result.rejected_count == 2

    assert result.accepted_symbols == ("PASS",)
    assert result.rejected_symbols == ("LOW", "DOWN")


def test_pipeline_preserves_candidate_order() -> None:
    result = run_candidate_pipeline(
        [
            make_candidate("CCC"),
            make_candidate("AAA"),
            make_candidate("BBB"),
        ],
        FilterSettings(),
        source_name="test_source",
    )

    assert result.accepted_symbols == (
        "CCC",
        "AAA",
        "BBB",
    )


def test_pipeline_exposes_rejection_reasons() -> None:
    result = run_candidate_pipeline(
        [
            make_candidate(
                "FAIL",
                price=0.25,
                volume=100,
            )
        ],
        FilterSettings(
            min_price=1.0,
            min_volume=1_000,
        ),
        source_name="test_source",
    )

    decision = result.decision_for("fail")

    assert decision is not None
    assert not decision.accepted
    assert len(decision.reasons) == 2
    assert "price" in decision.reasons[0]
    assert "volume" in decision.reasons[1]


def test_pipeline_handles_empty_candidate_collection() -> None:
    evaluated_at = datetime(
        2026,
        8,
        2,
        18,
        0,
        tzinfo=timezone.utc,
    )

    result = run_candidate_pipeline(
        [],
        FilterSettings(),
        source_name="empty_source",
        evaluated_at=evaluated_at,
    )

    assert result.evaluated_at == evaluated_at
    assert result.input_count == 0
    assert result.accepted == ()
    assert result.rejected == ()


def test_pipeline_rejects_empty_source_name() -> None:
    with pytest.raises(
        ValueError,
        match="source_name cannot be empty",
    ):
        run_candidate_pipeline(
            [make_candidate("TEST")],
            FilterSettings(),
            source_name="   ",
        )
