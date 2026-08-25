"""Reusable ThinkOrSwim Watchlist transport helpers."""

from __future__ import annotations

import csv
import time
import shutil
import subprocess

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class ScannerStateLike(Protocol):
    ready: bool
    status: str
    loop_state: str
    running: bool
    paused: bool
    exports_suspended: bool


class ScannerControlResultLike(Protocol):
    successful: bool
    return_code: int


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


def run_watchlist_export(
    *,
    target_filename: str,
    root: Path | None,
    wait: float,
    command_builder: Callable[
        ...,
        tuple[str, ...],
    ] = build_watchlist_export_command,
    executable_finder: Callable[
        [str],
        str | None,
    ] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[tuple[str, ...], int]:
    """
    Run one explicit ThinkOrSwim Watchlist export command.
    """

    executable = executable_finder(
        "mb-scan-command"
    )

    if executable is None:
        raise RuntimeError(
            "mb-scan-command was not found on PATH."
        )

    command = command_builder(
        target_filename=target_filename,
        root=root,
        wait=wait,
        executable=executable,
    )

    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
        )

    except OSError as exc:
        raise RuntimeError(
            f"Could not run mb-scan-command: {exc}"
        ) from exc

    return command, completed.returncode


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


def wait_for_scanner_state(
    *,
    root: Path | None,
    expect_suspended: bool,
    timeout: float,
    preflight_checker: Callable[
        ...,
        ScannerStateLike,
    ],
    state_matcher: Callable[..., bool] = scanner_state_matches,
    state_poll_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ScannerStateLike:
    """
    Wait until scanner state matches the requested export state.
    """

    if timeout <= 0:
        raise ValueError(
            "Scanner state wait must be positive."
        )

    deadline = monotonic() + timeout

    while True:
        result = preflight_checker(
            root=root,
            allow_exports_suspended=(
                expect_suspended
            ),
        )

        if state_matcher(
            result,
            expect_suspended=expect_suspended,
        ):
            return result

        if monotonic() >= deadline:
            return result

        sleep(state_poll_seconds)


def resume_exports_with_retry(
    *,
    root: Path | None,
    wait: float,
    control_executor: Callable[
        ...,
        ScannerControlResultLike,
    ],
    state_waiter: Callable[
        ...,
        ScannerStateLike,
    ],
    state_matcher: Callable[..., bool] = scanner_state_matches,
    attempts: int,
    retry_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> ScannerStateLike:
    """
    Restore scheduled exports after a protected Watchlist operation.
    """

    last_return_code: int | None = None
    last_preflight: ScannerStateLike | None = None

    for attempt in range(
        1,
        attempts + 1,
    ):
        resume_result = control_executor(
            action="resume_exports",
            root=root,
            wait=wait,
        )

        last_return_code = (
            resume_result.return_code
        )

        if resume_result.successful:
            resumed_preflight = state_waiter(
                root=root,
                expect_suspended=False,
            )

            last_preflight = resumed_preflight

            if state_matcher(
                resumed_preflight,
                expect_suspended=False,
            ):
                return resumed_preflight

        if attempt < attempts:
            sleep(retry_seconds)

    detail = (
        getattr(
            last_preflight,
            "detail",
            "active scanner state was not observed",
        )
        if last_preflight is not None
        else "active scanner state was not observed"
    )

    raise RuntimeError(
        "Scanner exports may remain suspended "
        "after "
        f"{attempts} "
        "resume_exports attempt(s); "
        f"last exit code={last_return_code}; "
        f"last state: {detail}"
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
