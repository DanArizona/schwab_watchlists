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
from pathlib import Path


COMMAND_FOR_MODE = {
    "add": "add_wl_symbols",
    "replace": "replace_wl_symbols",
}


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
) -> Path:
    """Save a JSON record for one Watchlist operation."""

    now = created_at or datetime.now().astimezone()

    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")

    output_path = (
        resolved_output_dir
        / f"{timestamp}-wl-{mode}-run.json"
    )

    record = {
        "created_at": now.isoformat(timespec="seconds"),
        "mode": mode,
        "scanner_command": COMMAND_FOR_MODE[mode],
        "submitted": submitted,
        "symbol_count": len(symbols),
        "symbols": list(symbols),
        "command": list(command),
        "return_code": return_code,
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
    created_at: datetime | None = None,
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
        )

        return WatchlistSubmissionResult(
            mode=mode,
            scanner_command=COMMAND_FOR_MODE[mode],
            symbols=normalized_symbols,
            command=preview_command,
            submitted=False,
            return_code=None,
            run_record_path=run_record_path,
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
    )

    return WatchlistSubmissionResult(
        mode=mode,
        scanner_command=COMMAND_FOR_MODE[mode],
        symbols=normalized_symbols,
        command=live_command,
        submitted=True,
        return_code=completed.returncode,
        run_record_path=run_record_path,
    )
