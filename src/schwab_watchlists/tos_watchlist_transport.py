"""Reusable ThinkOrSwim Watchlist transport helpers."""

from __future__ import annotations

import csv
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class ScannerStateLike(Protocol):
    ready: bool
    status: str
    loop_state: str
    running: bool
    paused: bool
    exports_suspended: bool


def build_watchlist_export_command(
    *,
    target_filename: str,
    root: Path | None,
    wait: float,
    executable: str,
) -> tuple[str, ...]:
    """Build one explicit ThinkOrSwim Watchlist export command."""

    if wait <= 0:
        raise ValueError(
            "Watchlist export requires a positive scanner wait."
        )

    command = [
        executable,
        "export_wl",
        "--target-filename",
        target_filename,
    ]

    if root is not None:
        command.extend(
            [
                "--root",
                str(root.expanduser().resolve()),
            ]
        )

    command.extend(
        [
            "--wait",
            str(wait),
        ]
    )

    return tuple(command)


def wait_for_file(
    path: Path,
    *,
    timeout: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for a file to become visible."""

    deadline = monotonic() + timeout

    while monotonic() < deadline:
        if path.exists() and path.is_file():
            return True

        sleep(0.25)

    return path.exists() and path.is_file()


def scanner_state_matches(
    result: ScannerStateLike,
    *,
    expect_suspended: bool,
) -> bool:
    """Return whether scanner state matches the expected export state."""

    if expect_suspended:
        return (
            result.ready
            and result.status == "HEALTHY"
            and result.loop_state == "exports_suspended"
            and result.running
            and not result.paused
            and result.exports_suspended
        )

    return (
        result.ready
        and result.status == "HEALTHY"
        and result.loop_state == "idle"
        and result.running
        and not result.paused
        and not result.exports_suspended
    )


def read_watchlist_symbols(
    path: Path,
    *,
    open_timeout_s: float = 10.0,
    retry_interval_s: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> set[str]:
    """
    Read symbols from a ThinkOrSwim Watchlist export.

    ThinkOrSwim writes preamble rows before the CSV header, so locate
    the row whose first column is ``Symbol``.

    A file copied from El-Cheapo may become visible on MasterBot
    slightly before its writer releases it. Retry transient
    PermissionError failures.
    """

    deadline = monotonic() + open_timeout_s

    while True:
        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as input_file:
                rows = list(csv.reader(input_file))

            break

        except PermissionError:
            if monotonic() >= deadline:
                raise

            sleep(retry_interval_s)

    symbols: set[str] = set()
    header_found = False

    for row in rows:
        if not row:
            continue

        first_column = row[0].strip()

        if not header_found:
            if first_column == "Symbol":
                header_found = True

            continue

        if first_column:
            symbols.add(first_column.upper())

    if not header_found:
        raise RuntimeError(
            "Watchlist verification CSV does not "
            f"contain a Symbol header: {path}"
        )

    return symbols
