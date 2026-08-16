"""
Reusable ThinkOrSwim Watchlist submission support.

This module prepares and optionally publishes add_wl_symbols and
replace_wl_symbols commands through mb-scan-command.

Dry-run is the default.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from scanner_preflight import (
    ScannerPreflightResult,
    check_scanner_ready,
)

COMMAND_FOR_MODE = {
    "add": "add_wl_symbols",
    "replace": "replace_wl_symbols",
}
RECORD_ORIGIN_SCHWAB_MOVERS = "schwab_movers"
RECORD_ORIGIN_DIRECT_SUBMISSION = "direct_submission"
RECORD_ORIGIN_PLAN_PREVIEW = "plan_preview"
RECORD_ORIGIN_PLAN_APPLICATION = "plan_application"
RECORD_ORIGIN_WATCHLIST_CYCLE = "watchlist_cycle"
ET_ZONE = ZoneInfo("America/New_York")


VALID_RECORD_ORIGINS = frozenset({
    RECORD_ORIGIN_SCHWAB_MOVERS,
    RECORD_ORIGIN_DIRECT_SUBMISSION,
    RECORD_ORIGIN_PLAN_PREVIEW,
    RECORD_ORIGIN_PLAN_APPLICATION,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
})


def normalize_cycle_id(
    cycle_id: str | None,
) -> str | None:
    """Validate an optional cycle identifier for records and filenames."""

    if cycle_id is None:
        return None

    normalized = cycle_id.strip()

    if not normalized:
        raise ValueError(
            "Watchlist cycle_id cannot be empty."
        )

    if Path(normalized).name != normalized:
        raise ValueError(
            "Watchlist cycle_id cannot contain "
            "directory components."
        )

    if "/" in normalized or "\\" in normalized:
        raise ValueError(
            "Watchlist cycle_id cannot contain "
            "path separators."
        )

    return normalized


@dataclass(frozen=True, slots=True)
class WatchlistSubmissionResult:
    """Result of one previewed or published Watchlist operation."""

    mode: str
    scanner_command: str
    symbols: tuple[str, ...]
    command: tuple[str, ...]
    submitted: bool
    return_code: int | None
    run_record_path: Path
    preflight: ScannerPreflightResult | None

    @property
    def successful(self) -> bool:
        """Whether the preview or live operation was successful."""

        if not self.submitted:
            return True

        return self.return_code == 0


def normalize_symbols(
    values: Sequence[str],
) -> list[str]:
    """Uppercase and deduplicate symbols while preserving order."""

    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = value.replace(",", " ")

        for part in cleaned.split():
            symbol = part.strip().upper()

            if not symbol or symbol in seen:
                continue

            seen.add(symbol)
            normalized.append(symbol)

    return normalized


def build_watchlist_command(
    *,
    mode: str,
    symbols: Sequence[str],
    wait: float = 30.0,
    root: Path | None = None,
    executable: str = "mb-scan-command",
) -> tuple[str, ...]:
    """Build and validate an mb-scan-command command line."""

    if mode not in COMMAND_FOR_MODE:
        raise ValueError(
            f"Unsupported Watchlist mode: {mode}"
        )

    if wait < 0:
        raise ValueError("Watchlist wait cannot be negative.")

    normalized_symbols = normalize_symbols(symbols)

    if not normalized_symbols:
        raise ValueError(
            "At least one Watchlist symbol is required."
        )

    command = [
        executable,
        COMMAND_FOR_MODE[mode],
        "--symbols",
        *normalized_symbols,
    ]

    if root is not None:
        resolved_root = root.expanduser().resolve()

        command.extend([
            "--root",
            str(resolved_root),
        ])

    if wait > 0:
        command.extend([
            "--wait",
            str(wait),
        ])

    return tuple(command)


def save_watchlist_run_record(
    *,
    output_dir: Path,
    mode: str,
    symbols: Sequence[str],
    submitted: bool,
    command: Sequence[str],
    return_code: int | None,
    created_at: datetime | None = None,
    preflight: ScannerPreflightResult | None = None,
    source_plan_path: Path | None = None,
    source_plan_created_at: str | None = None,
    record_origin: str = RECORD_ORIGIN_DIRECT_SUBMISSION,
    cycle_id: str | None = None,
) -> Path:
    """Save a JSON record for one Watchlist operation."""

    if record_origin not in VALID_RECORD_ORIGINS:
        raise ValueError(
            "Unsupported Watchlist record origin: "
            f"{record_origin}"
        )
    normalized_cycle_id = normalize_cycle_id(
        cycle_id
    )

    now = created_at or datetime.now(ET_ZONE)

    if now.tzinfo is None:
        now = now.replace(tzinfo=ET_ZONE)
    else:
        now = now.astimezone(ET_ZONE)

    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")

    filename_prefix = (
        normalized_cycle_id
        if (
            normalized_cycle_id is not None
            and record_origin
            == RECORD_ORIGIN_WATCHLIST_CYCLE
        )
        else timestamp
    )

    output_path = (
        resolved_output_dir
        / f"{filename_prefix}-wl-{mode}-run.json"
    )


    resolved_source_plan = (
        source_plan_path.expanduser().resolve()
        if source_plan_path is not None
        else None
    )

    record = {
        "created_at": now.isoformat(timespec="seconds"),
        "record_origin": record_origin,
        "cycle_id": normalized_cycle_id,
        "mode": mode,
        "scanner_command": COMMAND_FOR_MODE[mode],
        "submitted": submitted,
        "symbol_count": len(symbols),
        "symbols": list(symbols),
        "command": list(command),
        "return_code": return_code,
        "scanner_preflight": (
            preflight.as_record()
            if preflight is not None
            else None
        ),
        "source_plan_file": (
            str(resolved_source_plan)
            if resolved_source_plan is not None
            else None
        ),
        "source_plan_created_at": source_plan_created_at,
    }

    with output_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output_file:
        json.dump(
            record,
            output_file,
            indent=2,
            ensure_ascii=False,
        )
        output_file.write("\n")

    return output_path


def submit_watchlist_symbols(
    *,
    mode: str,
    symbols: Sequence[str],
    submit: bool = False,
    wait: float = 30.0,
    root: Path | None = None,
    output_dir: Path,
    executable_finder: Callable[
        [str],
        str | None,
    ] = shutil.which,
    runner: Callable[
        ...,
        subprocess.CompletedProcess[str],
    ] = subprocess.run,
    preflight_checker: Callable[
        ...,
        ScannerPreflightResult,
    ] = check_scanner_ready,
    created_at: datetime | None = None,
    source_plan_path: Path | None = None,
    source_plan_created_at: str | None = None,
    record_origin: str = RECORD_ORIGIN_DIRECT_SUBMISSION,
    cycle_id: str | None = None,
) -> WatchlistSubmissionResult:
    """
    Preview or publish one Watchlist command.

    No external command is executed unless submit=True.
    """

    normalized_symbols = tuple(
        normalize_symbols(symbols)
    )

    preview_command = build_watchlist_command(
        mode=mode,
        symbols=normalized_symbols,
        wait=wait,
        root=root,
    )

    if not submit:
        run_record_path = save_watchlist_run_record(
            output_dir=output_dir,
            mode=mode,
            symbols=normalized_symbols,
            submitted=False,
            command=preview_command,
            return_code=None,
            created_at=created_at,
            preflight=None,
            source_plan_path=source_plan_path,
            source_plan_created_at=source_plan_created_at,
            record_origin=record_origin,
            cycle_id=cycle_id,
        )

        return WatchlistSubmissionResult(
            mode=mode,
            scanner_command=COMMAND_FOR_MODE[mode],
            symbols=normalized_symbols,
            command=preview_command,
            submitted=False,
            return_code=None,
            run_record_path=run_record_path,
            preflight=None,
        )

    preflight = preflight_checker(
        root=root,
    )

    if not preflight.ready:
        raise RuntimeError(
            f"Scanner preflight failed: "
            f"{preflight.detail}"
        )

    executable = executable_finder(
        "mb-scan-command"
    )

    if executable is None:
        raise RuntimeError(
            "mb-scan-command was not found on PATH."
        )

    live_command = build_watchlist_command(
        mode=mode,
        symbols=normalized_symbols,
        wait=wait,
        root=root,
        executable=executable,
    )

    try:
        completed = runner(
            list(live_command),
            check=False,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Could not run mb-scan-command: {exc}"
        ) from exc

    run_record_path = save_watchlist_run_record(
        output_dir=output_dir,
        mode=mode,
        symbols=normalized_symbols,
        submitted=True,
        command=live_command,
        return_code=completed.returncode,
        created_at=created_at,
        preflight=preflight,
        source_plan_path=source_plan_path,
        source_plan_created_at=source_plan_created_at,
        record_origin=record_origin,
        cycle_id=cycle_id,
    )

    return WatchlistSubmissionResult(
        mode=mode,
        scanner_command=COMMAND_FOR_MODE[mode],
        symbols=normalized_symbols,
        command=live_command,
        submitted=True,
        return_code=completed.returncode,
        run_record_path=run_record_path,
        preflight=preflight,
)