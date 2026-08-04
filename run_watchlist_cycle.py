"""Run one manually initiated Watchlist cycle."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import SecureSchwabConfigError

from candidate_filters import FilterSettings, MissingFieldPolicy
from schwab_movers_source import (
    FREQUENCY_CHOICES,
    MARKET_CHOICES,
    SORT_CHOICES,
    fetch_schwab_movers,
    load_schwab_movers_replay,
)
from watchlist_cycle import (
    CYCLE_STATUS_NO_CHANGE,
    CYCLE_STATUS_PLAN_CREATED,
    run_schwab_movers_cycle,
)
from watchlist_plan import load_watchlist_plan
from watchlist_submission import (
    COMMAND_FOR_MODE,
    RECORD_ORIGIN_PLAN_APPLICATION,
    WatchlistSubmissionResult,
    submit_watchlist_symbols,
)

DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"


def resolve_ecfg_path(explicit_path: Path | None) -> Path:
    """Resolve the encrypted Schwab configuration path."""

    if explicit_path is not None:
        return explicit_path.expanduser()

    schwab_ecfg = os.environ.get("MB_SCHWAB_ECFG")

    if schwab_ecfg:
        return Path(schwab_ecfg).expanduser()

    vault = os.environ.get("MB_VAULT")

    if vault:
        return Path(vault).expanduser() / DEFAULT_ECFG_NAME

    return Path(DEFAULT_ECFG_NAME)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one manual Watchlist cycle from either the live Schwab "
            "Movers endpoint or a saved response. The command creates "
            "candidate outputs, a frozen Watchlist plan, and a cycle audit "
            "record. Live publication requires the explicit --submit option "
            "and is never allowed with --replay."
        )
    )

    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="RAW_JSON",
        help=(
            "Use a saved Schwab Movers JSON response instead of contacting "
            "Schwab. Without this option, the cycle retrieves live Movers."
        ),
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "After a live Schwab cycle creates its frozen plan, apply that "
            "exact plan through the normal scanner-readiness preflight. "
            "Cannot be used with --replay."
        ),
    )
    parser.add_argument(
        "--force-submit",
        action="store_true",
        help=(
            "Submit even when the generated replacement exactly matches "
            "the most recent successful cycle replacement. Requires "
            "--submit and cannot be used with --replay."
        ),
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
        "--ecfg",
        type=Path,
        default=None,
        help=(
            "Path to secure_schwabdev.ecfg for a live cycle. Defaults to "
            "MB_SCHWAB_ECFG, then MB_VAULT\\secure_schwabdev.ecfg, then "
            "the current directory."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Schwab client request timeout in seconds. Default: 10.",
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
            "Scanner command root recorded in the frozen plan and used for "
            "live submission when --submit is present."
        ),
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Maximum scanner processing wait. Default: 30.",
    )

    return parser


def apply_frozen_cycle_plan(
    *,
    plan_path: Path,
    expected_cycle_id: str,
    output_dir: Path,
    root: Path | None,
    wait: float,
    submitter: Callable[..., WatchlistSubmissionResult] | None = None,
) -> WatchlistSubmissionResult:
    """Apply the exact frozen plan created by the current cycle."""

    plan = load_watchlist_plan(plan_path)

    if plan.cycle_id != expected_cycle_id:
        raise ValueError(
            "Frozen Watchlist plan cycle_id does not match "
            f"the active cycle: {plan.cycle_id!r} != "
            f"{expected_cycle_id!r}."
        )

    chosen_submitter = submitter or submit_watchlist_symbols

    return chosen_submitter(
        mode=plan.mode,
        symbols=plan.symbols,
        submit=True,
        wait=wait,
        root=root,
        output_dir=output_dir,
        source_plan_path=plan.source_path,
        source_plan_created_at=plan.created_at,
        record_origin=RECORD_ORIGIN_PLAN_APPLICATION,
        cycle_id=plan.cycle_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    replay_path: Path | None = None

    if args.force_submit and not args.submit:
        print(
            "ERROR: --force-submit requires --submit.",
            file=sys.stderr,
        )
        return 2

    if args.submit and args.replay is not None:
        print(
            "ERROR: --submit cannot be used with --replay. "
            "Replay cycles are permanently dry-run only.",
            file=sys.stderr,
        )
        return 2

    if args.replay is not None:
        replay_path = args.replay.expanduser().resolve()

        if not replay_path.is_file():
            print(
                f"ERROR: Replay file does not exist: {replay_path}",
                file=sys.stderr,
            )
            return 2

    if args.wait < 0:
        print("ERROR: --wait cannot be negative.", file=sys.stderr)
        return 2

    if args.timeout < 1:
        print("ERROR: --timeout must be at least 1.", file=sys.stderr)
        return 2

    try:
        filter_settings = FilterSettings(
            min_price=args.min_price,
            max_price=args.max_price,
            min_volume=args.min_volume,
            min_percent_change=args.min_percent_change,
            max_percent_change=args.max_percent_change,
            max_results=args.limit,
            missing_field_policy=MissingFieldPolicy(
                args.missing_field_policy
            ),
        )
    except ValueError as exc:
        print(f"ERROR: Invalid filter settings: {exc}", file=sys.stderr)
        return 2

    client = None
    ecfg_path: Path | None = None

    try:
        if replay_path is not None:
            input_mode = "replay"
            batch = load_schwab_movers_replay(
                replay_path,
                market=args.market,
                sort_name=args.sort,
                frequency=args.frequency,
            )
        else:
            input_mode = "api"
            ecfg_path = resolve_ecfg_path(args.ecfg).resolve()

            if not ecfg_path.is_file():
                print(
                    "ERROR: Schwab encrypted configuration does not exist: "
                    f"{ecfg_path}",
                    file=sys.stderr,
                )
                return 2

            password = getpass.getpass("ecfg password: ")
            client = make_secure_schwab_client(
                ecfg_path,
                password,
                timeout=args.timeout,
            )
            batch = fetch_schwab_movers(
                client,
                market=args.market,
                sort_name=args.sort,
                frequency=args.frequency,
            )

        result = run_schwab_movers_cycle(
            batch=batch,
            filter_settings=filter_settings,
            mode=args.mode,
            output_dir=args.output_dir,
            input_mode=input_mode,
            replay_path=replay_path,
            root=args.root,
            wait=args.wait,
            submit_requested=args.submit,
            suppress_unchanged=(
                args.submit and not args.force_submit
            ),
        )
    except SchwabdevNotInstalledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except SecureSchwabConfigError as exc:
        print(
            f"ERROR: Invalid Schwab configuration: {exc}",
            file=sys.stderr,
        )
        return 4
    except ValueError as exc:
        print(f"ERROR: Watchlist cycle failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"ERROR: Watchlist cycle failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    print("Watchlist cycle")
    print("=" * 72)
    print(f"Cycle ID         : {result.cycle_id}")

    if replay_path is not None:
        print("Input source     : Schwab Movers replay")
        print(f"Replay file      : {replay_path}")
    else:
        print("Input source     : Live Schwab API")
        print(f"Encrypted config : {ecfg_path}")
        print(f"Request URL      : {batch.request_url}")
        print(f"HTTP status      : {batch.status_code}")

    print(f"Strategy         : {result.strategy_name}")
    print(f"Watchlist mode   : {result.watchlist_mode}")
    print(
        f"Submission       : "
        f"{'LIVE' if args.submit else 'DRY RUN'}"
    )
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

    if not args.submit:
        print("No Watchlist command was published.")
        return 0 if result.status == CYCLE_STATUS_PLAN_CREATED else 2

    if result.status == CYCLE_STATUS_NO_CHANGE:
        match = result.no_change_match
        assert match is not None
        print()
        print("No Watchlist change detected.")
        print(f"Matches cycle     : {match.cycle_id}")
        print(f"Previously applied: {match.applied_at}")
        print(f"Prior plan        : {match.source_plan_path}")
        print(f"Prior run         : {match.application_run_path}")
        print("No Watchlist command was published.")
        return 0

    print()
    print("Applying exact frozen cycle plan...")

    try:
        application = apply_frozen_cycle_plan(
            plan_path=result.watchlist_plan.run_record_path,
            expected_cycle_id=result.cycle_id,
            output_dir=args.output_dir,
            root=args.root,
            wait=args.wait,
        )
    except ValueError as exc:
        print(f"ERROR: Frozen plan application failed: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: Frozen plan application failed: {exc}", file=sys.stderr)
        return 3

    if application.preflight is not None:
        print(
            f"Scanner preflight       : "
            f"{'READY' if application.preflight.ready else 'NOT READY'}"
        )
        print(f"Scanner status          : {application.preflight.status}")
        print(f"Scanner root            : {application.preflight.root}")

    print(f"mb-scan-command exit code: {application.return_code}")
    print(f"Application record       : {application.run_record_path}")

    if not application.successful:
        print(
            "Cycle Watchlist command was not reported as successfully "
            "processed.",
            file=sys.stderr,
        )
        return application.return_code or 1

    print("Frozen cycle plan was reported as processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
