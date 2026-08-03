"""
Preview or submit a reviewed Watchlist dry-run plan.

Dry-run is the default. Use --submit to publish the exact symbols
stored in the plan.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from watchlist_plan import load_watchlist_plan
from watchlist_submission import (
    RECORD_ORIGIN_PLAN_APPLICATION,
    RECORD_ORIGIN_PLAN_PREVIEW,
    build_watchlist_command,
    submit_watchlist_symbols,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or submit the exact mode and symbols "
            "stored in a reviewed Watchlist dry-run record."
        )
    )

    parser.add_argument(
        "--plan",
        type=Path,
        required=True,
        help=(
            "Path to an unsubmitted "
            "*-wl-add-run.json or "
            "*-wl-replace-run.json file."
        ),
    )

    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "Publish the Watchlist command. "
            "Without this option, only preview it."
        ),
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Maximum processing wait. Default: 30.",
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root. By default, "
            "mb-scan-command uses MB_SCAN_CONTROL."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "output"
        ),
        help="Directory for the new submission run record.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.wait < 0:
        print(
            "ERROR: --wait cannot be negative.",
            file=sys.stderr,
        )
        return 2

    try:
        plan = load_watchlist_plan(args.plan)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        preview_command = build_watchlist_command(
            mode=plan.mode,
            symbols=plan.symbols,
            wait=args.wait,
            root=args.root,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Watchlist plan")
    print("=" * 72)
    print(f"Plan file        : {plan.source_path}")
    print(f"Plan created     : {plan.created_at}")
    print(
        f"Cycle ID         : "
        f"{plan.cycle_id or '(none)'}"
    )
    print(f"Mode             : {plan.mode}")
    print(f"Scanner command  : {plan.scanner_command}")
    print(f"Symbol count     : {len(plan.symbols)}")
    print(
        f"Submission       : "
        f"{'LIVE' if args.submit else 'DRY RUN'}"
    )
    print(
        "Command root     : "
        f"{args.root if args.root is not None else os.environ.get('MB_SCAN_CONTROL', '(not set)')}"
    )

    print()
    print("Frozen plan symbols:")
    print(" ".join(plan.symbols))

    print()
    print("Command:")
    print(
        subprocess.list2cmdline(
            list(preview_command)
        )
    )

    if args.submit:
        print()
        print(
            "Checking scanner readiness "
            "and publishing reviewed plan..."
        )

    try:
        result = submit_watchlist_symbols(
            mode=plan.mode,
            symbols=plan.symbols,
            submit=args.submit,
            wait=args.wait,
            root=args.root,
            output_dir=args.output_dir,
            source_plan_path=plan.source_path,
            source_plan_created_at=plan.created_at,
            record_origin=(
                RECORD_ORIGIN_PLAN_APPLICATION
                if args.submit
                else RECORD_ORIGIN_PLAN_PREVIEW
            ),
            cycle_id=plan.cycle_id,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    print()

    if not result.submitted:
        print("No command was published.")
        print(
            f"New run record   : "
            f"{result.run_record_path}"
        )
        return 0

    if result.preflight is not None:
        print(
            f"Scanner preflight       : "
            f"{'READY' if result.preflight.ready else 'NOT READY'}"
        )
        print(
            f"Scanner status          : "
            f"{result.preflight.status}"
        )
        print(
            f"Scanner root            : "
            f"{result.preflight.root}"
        )

    print(
        f"mb-scan-command exit code: "
        f"{result.return_code}"
    )
    print(
        f"New run record           : "
        f"{result.run_record_path}"
    )

    if not result.successful:
        print(
            "Watchlist command was not reported "
            "as successfully processed.",
            file=sys.stderr,
        )
        return result.return_code or 1

    print(
        "Reviewed Watchlist plan was reported "
        "as processed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
