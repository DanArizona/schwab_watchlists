"""Run one Watchlist cycle under a nonblocking execution lock."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import run_watchlist_cycle
from watchlist_cycle_lock import (
    WatchlistCycleLock,
    WatchlistCycleLockHeldError,
    default_watchlist_cycle_lock_path,
)


LOCK_HELD_EXIT_CODE = 75


def build_parser() -> argparse.ArgumentParser:
    """Extend the normal cycle parser with scheduled-run lock options."""

    parser = run_watchlist_cycle.build_parser()
    parser.description = (
        f"{parser.description} This scheduled-run wrapper also prevents "
        "overlapping invocations with an operating-system execution lock."
    )

    parser.add_argument(
        "--lock-file",
        type=Path,
        default=None,
        help=(
            "Execution-lock metadata file. Default: "
            "<output-dir>\\.watchlist-cycle.lock."
        ),
    )

    return parser


def _remove_lock_file_argument(
    argv: Sequence[str],
) -> list[str]:
    """Remove the wrapper-only --lock-file option before delegation."""

    forwarded: list[str] = []
    index = 0

    while index < len(argv):
        argument = argv[index]

        if argument == "--lock-file":
            index += 2
            continue

        if argument.startswith("--lock-file="):
            index += 1
            continue

        forwarded.append(argument)
        index += 1

    return forwarded


def _print_lock_owner(
    error: WatchlistCycleLockHeldError,
) -> None:
    """Print useful diagnostic information for a busy lock."""

    print(
        "ERROR: Another Watchlist cycle invocation "
        "is already running.",
        file=sys.stderr,
    )
    print(
        f"Lock file        : {error.lock_path}",
        file=sys.stderr,
    )

    metadata = error.metadata or {}

    if metadata.get("pid") is not None:
        print(
            f"Owner PID        : {metadata['pid']}",
            file=sys.stderr,
        )

    if metadata.get("hostname"):
        print(
            f"Owner host       : {metadata['hostname']}",
            file=sys.stderr,
        )

    if metadata.get("acquired_at"):
        print(
            f"Acquired at      : "
            f"{metadata['acquired_at']}",
            file=sys.stderr,
        )

    command = metadata.get("command")

    if isinstance(command, list) and command:
        print(
            f"Owner command    : "
            f"{' '.join(str(part) for part in command)}",
            file=sys.stderr,
        )

    print(
        "No Watchlist cycle was started.",
        file=sys.stderr,
    )


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Acquire the execution lock and delegate to the normal runner."""

    raw_argv = list(
        sys.argv[1:]
        if argv is None
        else argv
    )

    args = build_parser().parse_args(raw_argv)

    lock_path = (
        args.lock_file.expanduser().resolve(
            strict=False
        )
        if args.lock_file is not None
        else default_watchlist_cycle_lock_path(
            args.output_dir
        )
    )

    forwarded_argv = _remove_lock_file_argument(
        raw_argv
    )

    lock = WatchlistCycleLock(
        lock_path,
        command=(
            sys.executable,
            str(Path(__file__).resolve()),
            *raw_argv,
        ),
    )

    try:
        with lock:
            return run_watchlist_cycle.main(
                forwarded_argv
            )
    except WatchlistCycleLockHeldError as exc:
        _print_lock_owner(exc)
        return LOCK_HELD_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
