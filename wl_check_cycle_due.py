"""Report whether a Watchlist cycle is due under the current policy."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from watchlist_cycle_schedule import (
    evaluate_output_watchlist_cycle_schedule,
    format_watchlist_cycle_schedule_decision,
)


def _parse_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--at must be an ISO datetime."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "--at must include a UTC offset."
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report whether one Watchlist cycle is due. The default policy "
            "uses America/Chicago weekdays, an 08:30-15:00 session, one-minute "
            "cadence for the first 10 minutes, five-minute cadence through "
            "the first hour, and ten-minute cadence afterward. Exchange "
            "holidays are not yet modeled."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Directory containing cycle records. Default: project output.",
    )
    parser.add_argument(
        "--at",
        type=_parse_at,
        default=None,
        metavar="ISO_DATETIME",
        help=(
            "Evaluate at an explicit timezone-aware ISO datetime. Intended "
            "for testing; default is the current time."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        decision = evaluate_output_watchlist_cycle_schedule(
            args.output_dir,
            now=args.at,
        )
    except ValueError as exc:
        print(f"ERROR: Cycle schedule failed: {exc}", file=sys.stderr)
        return 2

    for line in format_watchlist_cycle_schedule_decision(decision):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
