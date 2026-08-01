"""
Source-neutral models for Watchlist symbol candidates.

A candidate may originate from Schwab Movers, a CSV file, a database query,
another API, or a future monitoring process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class MarketSession(str, Enum):
    """Market session associated with candidate data."""

    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    OVERNIGHT = "overnight"
    EXTENDED = "extended"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    """
    Normalized candidate produced by any symbol source.

    Percentage values are stored as percentage points:

        5.0 means 5%
        -2.5 means -2.5%

    The raw field preserves source-specific data without making the filtering
    layer depend on a particular API response format.
    """

    symbol: str
    source: str

    as_of: datetime | None = None
    session: MarketSession | None = None

    last_price: float | None = None
    regular_close: float | None = None
    percent_change: float | None = None

    volume: int | None = None
    trades: int | None = None
    market_share_percent: float | None = None

    description: str | None = None
    source_rank: int | None = None

    raw: Mapping[str, Any] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        normalized_source = self.source.strip()

        if not normalized_symbol:
            raise ValueError("Candidate symbol cannot be empty.")

        if not normalized_source:
            raise ValueError("Candidate source cannot be empty.")

        if self.volume is not None and self.volume < 0:
            raise ValueError("Candidate volume cannot be negative.")

        if self.trades is not None and self.trades < 0:
            raise ValueError("Candidate trades cannot be negative.")

        if self.source_rank is not None and self.source_rank < 1:
            raise ValueError("Candidate source_rank must be at least 1.")

        object.__setattr__(self, "symbol", normalized_symbol)
        object.__setattr__(self, "source", normalized_source)
