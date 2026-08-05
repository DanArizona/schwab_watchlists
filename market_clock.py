"""Canonical market-time helpers for Watchlist cycles and filenames."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MARKET_TIMEZONE_NAME = "America/New_York"
MARKET_TIMEZONE = ZoneInfo(MARKET_TIMEZONE_NAME)


def require_aware_datetime(
    value: datetime,
    *,
    field_name: str = "datetime",
) -> None:
    """Require a datetime with timezone/UTC-offset information."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must include timezone information."
        )


def to_market_time(
    value: datetime,
    *,
    field_name: str = "datetime",
) -> datetime:
    """Convert an aware datetime to the New York market clock."""

    require_aware_datetime(
        value,
        field_name=field_name,
    )
    return value.astimezone(MARKET_TIMEZONE)


def market_now() -> datetime:
    """Return the current time on the New York market clock."""

    return datetime.now(tz=MARKET_TIMEZONE)
