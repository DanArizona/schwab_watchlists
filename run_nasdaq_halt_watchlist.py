from __future__ import annotations

import argparse
import sys
import time

from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from zoneinfo import ZoneInfo

from mb_market_data.nasdaq_halts import fetch_trade_halts
from mb_market_data.nasdaq_halt_monitor import NasdaqHaltMonitor

from watchlist_submission import (
    RECORD_ORIGIN_DIRECT_SUBMISSION,
    build_watchlist_command,
    submit_watchlist_symbols,
)


ET_ZONE = ZoneInfo("America/New_York")

DEFAULT_POLL_SECONDS = 60
DEFAULT_FETCH_TIMEOUT = 60.0
DEFAULT_SCANNER_WAIT = 30.0
ET_ZONE = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET_ZONE)


def parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run Nasdaq volatility-halt symbols through "
            "the ThinkOrSwim Watchlist submission path."
        )
    )

    parser.add_argument(
        "--date",
        type=parse_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Replay Nasdaq halts for a historical date. "
            "If omitted, use the current RSS feed and "
            "today's ET date."
        ),
    )

    parser.add_argument(
        "--polls",
        type=int,
        default=1,
        metavar="COUNT",
        help=(
            "Number of Nasdaq polls to perform. "
            "Default: 1."
        ),
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        metavar="SECONDS",
        help=(
            "Seconds between polls. "
            "Must be at least 60. Default: 60."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_FETCH_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Nasdaq RSS fetch timeout. "
            "Default: 60."
        ),
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_SCANNER_WAIT,
        metavar="SECONDS",
        help=(
            "mb-scan-command processing wait used when "
            "building the dry-run submission. Default: 30."
        ),
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root. If omitted, "
            "MB_SCAN_CONTROL is used by the existing "
            "Watchlist submission layer."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "output"
            / "nasdaq_halt_watchlist"
        ),
        help="Directory for dry-run Watchlist run records.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.polls < 1:
        print(
            "ERROR: --polls must be at least 1.",
            file=sys.stderr,
        )
        return 2

    if args.interval < 60:
        print(
            "ERROR: --interval must be at least 60 seconds.",
            file=sys.stderr,
        )
        return 2

    if args.timeout <= 0:
        print(
            "ERROR: --timeout must be positive.",
            file=sys.stderr,
        )
        return 2

    if args.wait < 0:
        print(
            "ERROR: --wait cannot be negative.",
            file=sys.stderr,
        )
        return 2

    monitor = NasdaqHaltMonitor()

    if args.date is None:
        mode_name = "CURRENT"
    else:
        mode_name = "HISTORICAL REPLAY"

    print("Nasdaq Halt -> Watchlist DRY RUN")
    print("=" * 70)
    print(f"Mode           : {mode_name}")

    if args.date is not None:
        print(
            f"Replay date    : "
            f"{args.date.isoformat()}"
        )

    print(f"Polls          : {args.polls}")
    print(f"Poll interval  : {args.interval:g} seconds")
    print(f"Fetch timeout  : {args.timeout:g} seconds")
    print("Reason codes   : LUDP, M")
    print("Watchlist mode : add")
    print("Submission     : DRY RUN ONLY")
    print()

    for poll_number in range(1, args.polls + 1):
        poll_time = now_et()

        if args.date is None:
            session_date = poll_time.date()
        else:
            session_date = args.date

        print(
            f"[{poll_time.strftime('%Y-%m-%d %H:%M:%S ET')}] "
            f"Poll {poll_number}"
        )

        print(
            f"  Session date : "
            f"{session_date.isoformat()}"
        )

        try:
            feed = fetch_trade_halts(
                halt_date=args.date,
                timeout=args.timeout,
            )

        except Exception as exc:
            print(
                f"  Nasdaq fetch ERROR: "
                f"{type(exc).__name__}: {exc}"
            )

        else:
            new_symbols = monitor.new_symbols(
                feed.records,
                session_date=session_date,
            )

            print(
                f"  Feed mode    : "
                f"{feed.retrieval_mode}"
            )

            print(
                f"  Feed records : "
                f"{len(feed.records)}"
            )

            print(
                f"  Seen symbols : "
                f"{len(monitor.seen_symbols)}"
            )

            print(
                f"  New symbols  : "
                f"{len(new_symbols)}"
            )

            if new_symbols:
                print(
                    "  Symbols      : "
                    + " ".join(new_symbols)
                )

                preview_command = build_watchlist_command(
                    mode="add",
                    symbols=new_symbols,
                    wait=args.wait,
                    root=args.root,
                )

                print(
                    "  Command      : "
                    + " ".join(
                        str(part)
                        for part in preview_command
                    )
                )

                try:
                    result = submit_watchlist_symbols(
                        mode="add",
                        symbols=new_symbols,
                        submit=False,
                        wait=args.wait,
                        root=args.root,
                        output_dir=args.output_dir,
                        record_origin=(
                            RECORD_ORIGIN_DIRECT_SUBMISSION
                        ),
                    )

                except (ValueError, RuntimeError) as exc:
                    print(
                        f"  Dry-run ERROR: "
                        f"{type(exc).__name__}: {exc}"
                    )

                else:
                    print(
                        f"  Run record   : "
                        f"{result.run_record_path}"
                    )

                    print(
                        "  Published    : NO"
                    )

        print()

        if poll_number < args.polls:
            time.sleep(
                args.interval
            )

    print("Dry run complete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
