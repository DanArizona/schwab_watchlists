"""Run one manually initiated, replay-backed Watchlist cycle."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from candidate_filters import FilterSettings, MissingFieldPolicy
from schwab_movers_source import (
    FREQUENCY_CHOICES,
    MARKET_CHOICES,
    SORT_CHOICES,
    load_schwab_movers_replay,
)
from watchlist_cycle import (
    CYCLE_STATUS_PLAN_CREATED,
    run_schwab_movers_cycle,
)
from watchlist_submission import COMMAND_FOR_MODE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one manual, dry-run Watchlist cycle from a saved "
            "Schwab Movers response. The command creates candidate outputs, "
            "a frozen Watchlist plan, and a cycle audit record. It never "
            "publishes a live scanner command."
        )
    )

    parser.add_argument(
        "--replay",
        type=Path,
        required=True,
        metavar="RAW_JSON",
        help="Saved Schwab Movers JSON response used as cycle input.",
    )
    parser.add_argument(
        "--market",
        choices=MARKET_CHOICES,
        default="NASDAQ",
        help="Mover market or index. Default: NASDAQ.",
    )
    parser.add_argument(
        "--sort",
        choices=SORT_CHOICES,
        default="PERCENT_CHANGE_UP",
        help="Mover ordering. Default: PERCENT_CHANGE_UP.",
    )
    parser.add_argument(
        "--frequency",
        type=int,
        choices=FREQUENCY_CHOICES,
        default=5,
        help="Schwab Movers frequency selector. Default: 5.",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(COMMAND_FOR_MODE),
        required=True,
        help="Frozen Watchlist plan operation: add or replace.",
    )
    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument("--min-volume", type=int, default=None)
    parser.add_argument("--min-percent-change", type=float, default=None)
    parser.add_argument("--max-percent-change", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--missing-field-policy",
        choices=tuple(policy.value for policy in MissingFieldPolicy),
        default=MissingFieldPolicy.REJECT.value,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Directory for cycle outputs. Default: project output folder.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root recorded in the frozen command. "
            "No command is published."
        ),
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Wait value recorded in the frozen command. Default: 30.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    replay_path = args.replay.expanduser().resolve()

    if not replay_path.is_file():
        print(f"ERROR: Replay file does not exist: {replay_path}", file=sys.stderr)
        return 2

    if args.wait < 0:
        print("ERROR: --wait cannot be negative.", file=sys.stderr)
        return 2

    try:
        filter_settings = FilterSettings(
            min_price=args.min_price,
            max_price=args.max_price,
            min_volume=args.min_volume,
            min_percent_change=args.min_percent_change,
            max_percent_change=args.max_percent_change,
            max_results=args.limit,
            missing_field_policy=MissingFieldPolicy(args.missing_field_policy),
        )
        batch = load_schwab_movers_replay(
            replay_path,
            market=args.market,
            sort_name=args.sort,
            frequency=args.frequency,
        )
        result = run_schwab_movers_cycle(
            batch=batch,
            filter_settings=filter_settings,
            mode=args.mode,
            output_dir=args.output_dir,
            input_mode="replay",
            replay_path=replay_path,
            root=args.root,
            wait=args.wait,
        )
    except ValueError as exc:
        print(f"ERROR: Watchlist cycle failed: {exc}", file=sys.stderr)
        return 2

    print("Watchlist cycle")
    print("=" * 72)
    print(f"Cycle ID         : {result.cycle_id}")
    print(f"Input source     : Schwab Movers replay")
    print(f"Replay file      : {replay_path}")
    print(f"Strategy         : {result.strategy_name}")
    print(f"Watchlist mode   : {result.watchlist_mode}")
    print(f"Input candidates : {result.pipeline_result.input_count}")
    print(f"Accepted         : {result.pipeline_result.accepted_count}")
    print(f"Rejected         : {result.pipeline_result.rejected_count}")
    print(f"Status           : {result.status}")
    print()
    print("Accepted symbols:")
    print(" ".join(result.pipeline_result.accepted_symbols) or "(none)")
    print()
    print(f"Candidate record : {result.candidate_outputs.run_json}")
    print(f"Cycle record     : {result.cycle_record_path}")

    if result.watchlist_plan is None:
        print("Watchlist plan   : not created")
        print("No Watchlist command was published.")
        return 2

    print(f"Watchlist plan   : {result.watchlist_plan.run_record_path}")
    print("No Watchlist command was published.")

    return 0 if result.status == CYCLE_STATUS_PLAN_CREATED else 2


if __name__ == "__main__":
    raise SystemExit(main())
