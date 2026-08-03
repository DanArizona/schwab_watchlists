"""
Load and validate reviewed Watchlist dry-run plans.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchlist_submission import (
    COMMAND_FOR_MODE,
    normalize_symbols,
)


@dataclass(frozen=True, slots=True)
class WatchlistPlan:
    """One validated, unsubmitted Watchlist dry-run record."""

    source_path: Path
    created_at: str
    mode: str
    scanner_command: str
    symbols: tuple[str, ...]


def load_watchlist_plan(
    path: Path,
) -> WatchlistPlan:
    """
    Load a Watchlist dry-run record as a frozen submission plan.

    Only unsubmitted records are accepted. The command saved in the
    record is not executed; a new command is built from the validated
    mode and symbols when the plan is applied.
    """

    resolved_path = path.expanduser().resolve()

    try:
        with resolved_path.open(
            "r",
            encoding="utf-8",
        ) as input_file:
            data: Any = json.load(input_file)

    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Watchlist plan is not valid JSON: "
            f"{resolved_path}: {exc}"
        ) from exc

    except OSError as exc:
        raise ValueError(
            f"Could not read Watchlist plan "
            f"{resolved_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            "Watchlist plan must contain a JSON object."
        )

    if data.get("submitted") is not False:
        raise ValueError(
            "Watchlist plan must be an unsubmitted "
            "dry-run record."
        )

    mode = data.get("mode")

    if not isinstance(mode, str):
        raise ValueError(
            "Watchlist plan does not contain a valid mode."
        )

    mode = mode.strip().lower()

    if mode not in COMMAND_FOR_MODE:
        raise ValueError(
            f"Unsupported Watchlist plan mode: {mode}"
        )

    scanner_command = data.get("scanner_command")
    expected_command = COMMAND_FOR_MODE[mode]

    if scanner_command != expected_command:
        raise ValueError(
            "Watchlist plan scanner command does not "
            f"match mode {mode!r}."
        )

    raw_symbols = data.get("symbols")

    if (
        not isinstance(raw_symbols, list)
        or not all(
            isinstance(symbol, str)
            for symbol in raw_symbols
        )
    ):
        raise ValueError(
            "Watchlist plan does not contain a valid "
            "symbol list."
        )

    normalized_symbols = tuple(
        normalize_symbols(raw_symbols)
    )

    if not normalized_symbols:
        raise ValueError(
            "Watchlist plan contains no symbols."
        )

    if list(normalized_symbols) != raw_symbols:
        raise ValueError(
            "Watchlist plan symbols must already be "
            "uppercase, unique, and normalized."
        )

    created_at = data.get("created_at")

    if (
        not isinstance(created_at, str)
        or not created_at.strip()
    ):
        raise ValueError(
            "Watchlist plan does not contain a valid "
            "creation time."
        )

    return WatchlistPlan(
        source_path=resolved_path,
        created_at=created_at.strip(),
        mode=mode,
        scanner_command=expected_command,
        symbols=normalized_symbols,
    )
