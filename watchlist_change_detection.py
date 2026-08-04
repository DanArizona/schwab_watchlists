"""Detect whether a replacement Watchlist would change the applied state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from watchlist_submission import (
    RECORD_ORIGIN_PLAN_APPLICATION,
    normalize_symbols,
)


@dataclass(frozen=True, slots=True)
class AppliedWatchlistSnapshot:
    """Most recent successful cycle replacement known from audit records."""

    cycle_id: str
    applied_at: str
    mode: str
    symbols: tuple[str, ...]
    application_run_path: Path
    source_plan_path: Path


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _resolve_record_path(
    value: str,
    *,
    containing_file: Path,
) -> Path:
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = containing_file.parent / path

    return path.resolve(strict=False)


def find_latest_applied_replacement(
    output_dir: Path,
) -> AppliedWatchlistSnapshot | None:
    """Return the newest successful cycle replacement audit record."""

    resolved_output_dir = output_dir.expanduser().resolve(strict=False)

    if not resolved_output_dir.is_dir():
        return None

    latest: AppliedWatchlistSnapshot | None = None
    latest_time: datetime | None = None

    for run_path in resolved_output_dir.glob("*-wl-*-run.json"):
        data = _load_json_object(run_path)

        if data is None:
            continue

        if data.get("record_origin") != RECORD_ORIGIN_PLAN_APPLICATION:
            continue

        if data.get("submitted") is not True or data.get("return_code") != 0:
            continue

        if data.get("mode") != "replace":
            continue

        cycle_id = data.get("cycle_id")
        created_at = data.get("created_at")
        raw_symbols = data.get("symbols")
        source_plan_file = data.get("source_plan_file")
        parsed_time = _parse_time(created_at)

        if (
            not isinstance(cycle_id, str)
            or not cycle_id.strip()
            or parsed_time is None
            or not isinstance(raw_symbols, list)
            or not raw_symbols
            or not all(isinstance(symbol, str) for symbol in raw_symbols)
            or not isinstance(source_plan_file, str)
            or not source_plan_file.strip()
        ):
            continue

        normalized_symbols = tuple(normalize_symbols(raw_symbols))

        if list(normalized_symbols) != raw_symbols:
            continue

        if latest_time is not None and parsed_time <= latest_time:
            continue

        latest_time = parsed_time
        latest = AppliedWatchlistSnapshot(
            cycle_id=cycle_id.strip(),
            applied_at=created_at,
            mode="replace",
            symbols=normalized_symbols,
            application_run_path=run_path.resolve(strict=False),
            source_plan_path=_resolve_record_path(
                source_plan_file,
                containing_file=run_path,
            ),
        )

    return latest


def find_unchanged_replacement(
    *,
    output_dir: Path,
    mode: str,
    symbols: tuple[str, ...] | list[str],
) -> AppliedWatchlistSnapshot | None:
    """Return the latest applied replacement when mode and order match."""

    # An add operation does not establish the complete Watchlist state, so a
    # prior add record is never sufficient evidence that another add is a
    # no-op.
    if mode != "replace":
        return None

    normalized_symbols = tuple(normalize_symbols(symbols))

    if not normalized_symbols:
        return None

    latest = find_latest_applied_replacement(output_dir)

    if latest is None or latest.symbols != normalized_symbols:
        return None

    return latest
