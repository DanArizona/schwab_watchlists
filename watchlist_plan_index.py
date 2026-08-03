"""
Discover reviewed Watchlist plans and their application status.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from watchlist_submission import (
    COMMAND_FOR_MODE,
    RECORD_ORIGIN_SCHWAB_MOVERS,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
    normalize_symbols,
)
GENERATED_PLAN_ORIGINS = frozenset({
    RECORD_ORIGIN_SCHWAB_MOVERS,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
})


@dataclass(frozen=True, slots=True)
class WatchlistPlanEntry:
    """One original Watchlist dry-run plan."""

    plan_path: Path
    created_at: str
    mode: str
    symbols: tuple[str, ...]
    record_origin: str | None
    applied_run_path: Path | None = None
    applied_at: str | None = None

    @property
    def applied(self) -> bool:
        """Whether a linked successful live application exists."""

        return self.applied_run_path is not None

    @property
    def status(self) -> str:
        """Human-readable plan status."""

        return "APPLIED" if self.applied else "REVIEWED"


@dataclass(frozen=True, slots=True)
class WatchlistPlanIndex:
    """Result of scanning a directory for Watchlist plans."""

    output_dir: Path
    plans: tuple[WatchlistPlanEntry, ...]
    skipped_files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _ApplicationRecord:
    """Internal representation of one successful application."""

    run_path: Path
    created_at: str


def _canonical_path(path: Path) -> str:
    """Return a case-normalized absolute path for comparison."""

    resolved = path.expanduser().resolve(
        strict=False,
    )

    return os.path.normcase(str(resolved))


def _parse_time(value: Any) -> datetime | None:
    """Parse an ISO datetime used by run records."""

    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_json_object(
    path: Path,
) -> dict[str, Any] | None:
    """Load a JSON object, returning None for invalid files."""

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    return data


def discover_watchlist_plans(
    output_dir: Path,
    *,
    include_legacy: bool = False,
) -> WatchlistPlanIndex:
    """
    Discover original dry-run plans and linked applications.

    Current original plans have:

    - record_origin=schwab_movers or watchlist_cycle    
    - submitted=false; and
    - no source_plan_file.

    Records without record_origin are included only when
    include_legacy=True.

    A plan is considered applied only when a later record:

    - has submitted=true;
    - has return_code=0; and
    - names the original plan in source_plan_file.
    """

    resolved_output_dir = (
        output_dir
        .expanduser()
        .resolve(strict=False)
    )

    if not resolved_output_dir.is_dir():
        raise ValueError(
            f"Watchlist output directory does not exist: "
            f"{resolved_output_dir}"
        )

    records: list[
        tuple[Path, dict[str, Any]]
    ] = []

    skipped_files: list[Path] = []

    for path in sorted(
        resolved_output_dir.glob(
            "*-wl-*-run.json"
        )
    ):
        data = _load_json_object(path)

        if data is None:
            skipped_files.append(path)
            continue

        records.append((path, data))

    applications: dict[
        str,
        _ApplicationRecord,
    ] = {}

    for run_path, data in records:
        if data.get("submitted") is not True:
            continue

        if data.get("return_code") != 0:
            continue

        source_plan_file = data.get(
            "source_plan_file"
        )

        if (
            not isinstance(
                source_plan_file,
                str,
            )
            or not source_plan_file.strip()
        ):
            continue

        created_at = data.get("created_at")

        if _parse_time(created_at) is None:
            continue

        source_key = _canonical_path(
            Path(source_plan_file)
        )

        current = applications.get(source_key)

        if (
            current is None
            or _parse_time(created_at)
            > _parse_time(current.created_at)
        ):
            applications[source_key] = (
                _ApplicationRecord(
                    run_path=run_path.resolve(
                        strict=False
                    ),
                    created_at=created_at,
                )
            )

    plans: list[WatchlistPlanEntry] = []

    for plan_path, data in records:
        if data.get("submitted") is not False:
            continue

        source_plan_file = data.get(
            "source_plan_file"
        )

        if source_plan_file not in (
            None,
            "",
        ):
            # This is a derived dry-run made by
            # wl_apply_plan.py, not an original plan.
            continue


        record_origin = data.get(
            "record_origin"
        )

        if record_origin in GENERATED_PLAN_ORIGINS:
            pass
        elif (
            include_legacy
            and record_origin is None
        ):
            pass
        else:
            continue


        mode = data.get("mode")

        if (
            not isinstance(mode, str)
            or mode not in COMMAND_FOR_MODE
        ):
            continue

        scanner_command = data.get(
            "scanner_command"
        )

        if (
            scanner_command
            != COMMAND_FOR_MODE[mode]
        ):
            continue

        raw_symbols = data.get("symbols")

        if (
            not isinstance(raw_symbols, list)
            or not all(
                isinstance(symbol, str)
                for symbol in raw_symbols
            )
        ):
            continue

        symbols = tuple(
            normalize_symbols(raw_symbols)
        )

        if (
            not symbols
            or list(symbols) != raw_symbols
        ):
            continue

        created_at = data.get("created_at")

        if _parse_time(created_at) is None:
            continue

        resolved_plan_path = (
            plan_path.resolve(strict=False)
        )

        application = applications.get(
            _canonical_path(
                resolved_plan_path
            )
        )

        plans.append(
            WatchlistPlanEntry(
                plan_path=resolved_plan_path,
                created_at=created_at,
                mode=mode,
                symbols=symbols,
                record_origin=record_origin,                
                applied_run_path=(
                    application.run_path
                    if application is not None
                    else None
                ),
                applied_at=(
                    application.created_at
                    if application is not None
                    else None
                ),
            )
        )

    plans.sort(
        key=lambda plan: (
            _parse_time(plan.created_at)
            or datetime.min
        ),
        reverse=True,
    )

    return WatchlistPlanIndex(
        output_dir=resolved_output_dir,
        plans=tuple(plans),
        skipped_files=tuple(skipped_files),
    )
