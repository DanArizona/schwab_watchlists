"""
Reusable Charles Schwab Movers source adapter.

This module retrieves Schwab mover records and converts them into the
source-neutral SymbolCandidate model. It contains no command-line, GUI,
file-output, or ThinkOrSwim submission logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from candidate_model import SymbolCandidate


MARKET_CHOICES = (
    "$DJI",
    "$COMPX",
    "$SPX",
    "NYSE",
    "NASDAQ",
    "OTCBB",
    "INDEX_ALL",
    "EQUITY_ALL",
    "OPTION_ALL",
    "OPTION_PUT",
    "OPTION_CALL",
)

SORT_CHOICES = (
    "VOLUME",
    "TRADES",
    "PERCENT_CHANGE_UP",
    "PERCENT_CHANGE_DOWN",
)

FREQUENCY_CHOICES = (
    0,
    1,
    5,
    10,
    30,
    60,
)


class SchwabMoversClient(Protocol):
    """Minimum client interface required by this source adapter."""

    def movers(
        self,
        symbol: str,
        sort: str | None = None,
        frequency: int | None = None,
    ) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class SchwabMoversBatch:
    """One normalized response from the Schwab Movers endpoint."""

    market: str
    sort_name: str
    frequency: int
    requested_at: datetime

    request_url: str
    status_code: int

    records: tuple[Mapping[str, Any], ...]
    candidates: tuple[SymbolCandidate, ...]

    raw_data: Mapping[str, Any] = field(
        compare=False,
        repr=False,
    )


def optional_float(value: Any) -> float | None:
    """Convert a numeric value to float, excluding booleans."""

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


def optional_int(value: Any) -> int | None:
    """Convert a numeric value to int, excluding booleans."""

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return int(value)

    return None


def find_symbol_records(value: Any) -> list[Mapping[str, Any]]:
    """Recursively find dictionaries containing a nonempty symbol."""

    records: list[Mapping[str, Any]] = []

    if isinstance(value, Mapping):
        symbol = value.get("symbol")

        if isinstance(symbol, str) and symbol.strip():
            records.append(value)

        for child in value.values():
            records.extend(find_symbol_records(child))

    elif isinstance(value, list):
        for child in value:
            records.extend(find_symbol_records(child))

    return records


def deduplicate_records(
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Deduplicate records by uppercase symbol while preserving order."""

    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        raw_symbol = record.get("symbol")

        if not isinstance(raw_symbol, str):
            continue

        symbol = raw_symbol.strip().upper()

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)
        result.append(record)

    return result


def sort_mover_records(
    records: Sequence[Mapping[str, Any]],
    sort_name: str,
) -> list[Mapping[str, Any]]:
    """Sort returned records deterministically using Schwab fields."""

    if sort_name == "VOLUME":
        return sorted(
            records,
            key=lambda record: record.get("volume") or 0,
            reverse=True,
        )

    if sort_name == "TRADES":
        return sorted(
            records,
            key=lambda record: record.get("trades") or 0,
            reverse=True,
        )

    if sort_name == "PERCENT_CHANGE_UP":
        return sorted(
            records,
            key=lambda record: record.get("netPercentChange") or 0,
            reverse=True,
        )

    if sort_name == "PERCENT_CHANGE_DOWN":
        return sorted(
            records,
            key=lambda record: record.get("netPercentChange") or 0,
        )

    raise ValueError(f"Unsupported mover sort: {sort_name}")


def mover_record_to_candidate(
    record: Mapping[str, Any],
    *,
    source_rank: int,
    as_of: datetime,
) -> SymbolCandidate:
    """
    Convert one Schwab mover record into the common candidate model.

    Schwab returns netPercentChange as a decimal ratio. The candidate model
    stores percentage points, so 0.0204 becomes 2.04.
    """

    raw_symbol = record.get("symbol")

    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        raise ValueError("Schwab mover record does not contain a symbol.")

    raw_description = record.get("description")
    description = (
        raw_description.strip()
        if isinstance(raw_description, str) and raw_description.strip()
        else None
    )

    percent_ratio = optional_float(record.get("netPercentChange"))
    percent_change = (
        percent_ratio * 100.0
        if percent_ratio is not None
        else None
    )

    return SymbolCandidate(
        symbol=raw_symbol,
        source="schwab_movers",
        as_of=as_of,

        # The endpoint does not identify the session represented by its
        # snapshot values, so we intentionally do not guess.
        session=None,

        last_price=optional_float(record.get("lastPrice")),
        regular_close=None,
        percent_change=percent_change,

        volume=optional_int(record.get("volume")),
        trades=optional_int(record.get("trades")),
        market_share_percent=optional_float(record.get("marketShare")),

        description=description,
        source_rank=source_rank,
        raw=dict(record),
    )


def fetch_schwab_movers(
    client: SchwabMoversClient,
    *,
    market: str,
    sort_name: str,
    frequency: int,
    as_of: datetime | None = None,
) -> SchwabMoversBatch:
    """
    Retrieve, validate, order, and normalize one Schwab Movers response.
    """

    if market not in MARKET_CHOICES:
        raise ValueError(f"Unsupported mover market: {market}")

    if sort_name not in SORT_CHOICES:
        raise ValueError(f"Unsupported mover sort: {sort_name}")

    if frequency not in FREQUENCY_CHOICES:
        raise ValueError(f"Unsupported mover frequency: {frequency}")

    requested_at = as_of or datetime.now().astimezone()

    response = client.movers(
        symbol=market,
        sort=sort_name,
        frequency=frequency,
    )

    request_url = str(getattr(response, "url", ""))
    status_code = int(response.status_code)

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(
            f"Schwab Movers response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, Mapping):
        raise ValueError(
            "Schwab Movers response must have a dictionary "
            "as its top-level JSON value."
        )

    discovered_records = find_symbol_records(data)
    unique_records = deduplicate_records(discovered_records)

    ordered_records = sort_mover_records(
        unique_records,
        sort_name,
    )

    candidates = tuple(
        mover_record_to_candidate(
            record,
            source_rank=index,
            as_of=requested_at,
        )
        for index, record in enumerate(
            ordered_records,
            start=1,
        )
    )

    return SchwabMoversBatch(
        market=market,
        sort_name=sort_name,
        frequency=frequency,
        requested_at=requested_at,
        request_url=request_url,
        status_code=status_code,
        records=tuple(ordered_records),
        candidates=candidates,
        raw_data=data,
    )
