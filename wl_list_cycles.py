"""List Watchlist cycles and their derived operational state."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from watchlist_cycle_index import (
    PENDING_CYCLE_STATES,
    VALID_CYCLE_STATES,
    WatchlistCycleEntry,
    discover_watchlist_cycles,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List Watchlist cycles and derive whether each plan "
            "was created, previewed, or successfully applied."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(Path(__file__).resolve().parent / "output"),
        help=(
            "Directory containing cycle and Watchlist run records. "
            "Default: project output."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of cycles to display. Default: 10.",
    )
    parser.add_argument(
        "--state",
        choices=tuple(
            sorted(state.lower() for state in VALID_CYCLE_STATES)
        ),
        default=None,
        help="Display only cycles in this derived state.",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help=(
            "Display only cycles whose plans have not been "
            "successfully applied."
        ),
    )
    return parser


def print_cycle(cycle: WatchlistCycleEntry) -> None:
    """Print one cycle summary."""

    print(
        f"[{cycle.state}] {cycle.cycle_id}  "
        f"started={cycle.started_at}  "
        f"mode={cycle.watchlist_mode}  "
        f"symbols={len(cycle.accepted_symbols)}"
    )
    print(
        f"  Source   : {cycle.candidate_source} "
        f"({cycle.input_mode})"
    )
    print(f"  Strategy : {cycle.strategy_name}")

    if cycle.accepted_symbols:
        print(
            "  Symbols  : "
            f"{' '.join(cycle.accepted_symbols)}"
        )

    print(f"  Cycle    : {cycle.cycle_record_path}")

    if cycle.plan_path is not None:
        print(f"  Plan     : {cycle.plan_path}")

    if cycle.preview_run_path is not None:
        print(f"  Previewed: {cycle.previewed_at}")
        print(f"  Preview  : {cycle.preview_run_path}")

    if cycle.application_run_path is not None:
        label = "Applied" if cycle.applied else "Attempted"
        when = (
            cycle.applied_at
            if cycle.applied
            else "unsuccessful"
        )
        print(f"  {label:<9}: {when}")
        print(
            f"  Run      : {cycle.application_run_path}"
        )
        print(
            "  Exit code: "
            f"{cycle.application_return_code}"
        )

    print()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.limit < 1:
        print(
            "ERROR: --limit must be at least 1.",
            file=sys.stderr,
        )
        return 2

    try:
        index = discover_watchlist_cycles(
            args.output_dir
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    cycles = list(index.cycles)

    if args.state is not None:
        requested_state = args.state.upper()
        cycles = [
            cycle
            for cycle in cycles
            if cycle.state == requested_state
        ]

    if args.pending_only:
        cycles = [
            cycle
            for cycle in cycles
            if cycle.state in PENDING_CYCLE_STATES
        ]

    displayed_cycles = cycles[: args.limit]

    print("Watchlist cycles")
    print("=" * 88)
    print(f"Output directory : {index.output_dir}")
    print(f"Cycles found     : {len(cycles)}")
    print(f"Displayed        : {len(displayed_cycles)}")

    if index.skipped_files:
        print(f"Invalid files    : {len(index.skipped_files)}")

    print()

    if not displayed_cycles:
        print("No matching Watchlist cycles were found.")
        return 0

    for cycle in displayed_cycles:
        print_cycle(cycle)

    if index.skipped_files:
        print(
            "Warning: invalid cycle or Watchlist records "
            "were skipped.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
