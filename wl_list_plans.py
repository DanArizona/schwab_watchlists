"""
List recent reviewed Watchlist plans and application status.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from watchlist_plan_index import (
    WatchlistPlanEntry,
    discover_watchlist_plans,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List original Watchlist dry-run plans "
            "and show whether each was later applied "
            "through wl_apply_plan.py."
        )
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "output"
        ),
        help=(
            "Directory containing Watchlist run "
            "records. Default: project output."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help=(
            "Maximum number of plans to display. "
            "Default: 10."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=("add", "replace"),
        default=None,
        help="Display only plans of this mode.",
    )

    parser.add_argument(
        "--pending-only",
        action="store_true",
        help=(
            "Display only reviewed plans that have "
            "not been linked to a successful live "
            "application."
        ),
    )

    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help=(
            "Include older unclassified dry-run "
            "records that do not contain "
            "record_origin."
        ),
    )

    return parser


def print_plan(
    plan: WatchlistPlanEntry,
) -> None:
    """Print one reviewed plan."""

    print(
        f"[{plan.status}] "
        f"{plan.created_at}  "
        f"mode={plan.mode}  "
        f"symbols={len(plan.symbols)}"
    )
    print(
        f"  Symbols : "
        f"{' '.join(plan.symbols)}"
    )
    print(
        "  Origin  : "
        f"{plan.record_origin or 'legacy_unclassified'}"
    )
    print(f"  Plan    : {plan.plan_path}")

    if plan.applied:
        print(
            f"  Applied : {plan.applied_at}"
        )
        print(
            f"  Run     : "
            f"{plan.applied_run_path}"
        )

    print()


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.limit < 1:
        print(
            "ERROR: --limit must be at least 1.",
            file=sys.stderr,
        )
        return 2

    try:
        index = discover_watchlist_plans(
            args.output_dir,
            include_legacy=args.include_legacy,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    plans = list(index.plans)

    if args.mode is not None:
        plans = [
            plan
            for plan in plans
            if plan.mode == args.mode
        ]

    if args.pending_only:
        plans = [
            plan
            for plan in plans
            if not plan.applied
        ]

    displayed_plans = plans[: args.limit]

    print("Generated Watchlist plans")
    print("=" * 88)
    print(
        f"Output directory : "
        f"{index.output_dir}"
    )
    print(
        f"Plans found      : "
        f"{len(plans)}"
    )
    print(
        f"Displayed        : "
        f"{len(displayed_plans)}"
    )

    if index.skipped_files:
        print(
            f"Invalid files    : "
            f"{len(index.skipped_files)}"
        )

    print()

    if not displayed_plans:
        print(
            "No matching generated Watchlist "            
            "plans were found."
        )
        return 0

    for plan in displayed_plans:
        print_plan(plan)

    if index.skipped_files:
        print(
            "Warning: invalid Watchlist run "
            "records were skipped.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())