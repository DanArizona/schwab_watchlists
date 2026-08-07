"""Run a manually unlocked Watchlist controller for one market session."""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import SecureSchwabConfigError

from candidate_filters import FilterSettings, MissingFieldPolicy
from run_watchlist_cycle import (
    apply_frozen_cycle_plan,
    resolve_ecfg_path,
)
from schwab_movers_source import (
    FREQUENCY_CHOICES,
    MARKET_CHOICES,
    SORT_CHOICES,
    fetch_schwab_movers,
)
from watchlist_cycle import (
    CYCLE_STATUS_NO_CHANGE,
    WatchlistCycleResult,
    run_schwab_movers_cycle,
)
from watchlist_cycle_lock import (
    WatchlistCycleLock,
    WatchlistCycleLockHeldError,
    default_watchlist_cycle_lock_path,
)
from watchlist_cycle_schedule import (
    SCHEDULE_STATUS_NON_TRADING_DAY,
    SCHEDULE_STATUS_OUTSIDE_SESSION,
    WatchlistCycleScheduleDecision,
    evaluate_output_watchlist_cycle_schedule,
    format_watchlist_cycle_schedule_decision,
)
from watchlist_submission import COMMAND_FOR_MODE
from scanner_export_coordination import (
    apply_watchlist_plan_with_export_suspension,
)


LOCK_HELD_EXIT_CODE = 75


class ClosableSchwabClient(Protocol):
    """Minimum client behavior used by the daily controller."""

    def close(self) -> None:
        ...


class CycleLockFactory(Protocol):
    """Factory for the controller's process-lifetime execution lock."""

    def __call__(
        self,
        lock_path: Path,
        *,
        command: Sequence[str],
    ) -> AbstractContextManager[Any]:
        ...


def build_parser() -> argparse.ArgumentParser:
    """Build the daily-controller command-line parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Start one manually unlocked Watchlist controller for the "
            "current market session. The controller prompts once for the "
            "Schwab .ecfg password, keeps one Schwab client in memory, "
            "evaluates the New York market schedule repeatedly, and runs "
            "only due cycles. Live Watchlist publication still requires "
            "the explicit --submit option."
        )
    )

    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "Apply each due cycle's exact frozen plan through the normal "
            "scanner preflight. Without this option, due cycles remain "
            "dry-run only."
        ),
    )
    parser.add_argument(
        "--force-submit",
        action="store_true",
        help=(
            "Bypass unchanged-replacement suppression. Requires --submit."
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
        help="Watchlist operation for each frozen plan: add or replace.",
    )

    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument("--min-volume", type=int, default=None)
    parser.add_argument("--min-percent-change", type=float, default=None)
    parser.add_argument("--max-percent-change", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--missing-field-policy",
        choices=tuple(
            policy.value
            for policy in MissingFieldPolicy
        ),
        default=MissingFieldPolicy.REJECT.value,
    )

    parser.add_argument(
        "--ecfg",
        type=Path,
        default=None,
        help=(
            "Path to secure_schwabdev.ecfg. Defaults to "
            "MB_SCHWAB_ECFG, then "
            "MB_VAULT\\secure_schwabdev.ecfg, then the current "
            "directory."
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
        default=(
            Path(__file__).resolve().parent
            / "output"
        ),
        help="Directory for cycle outputs. Default: project output.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root used when --submit is present."
        ),
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Maximum scanner processing wait. Default: 30.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help=(
            "Seconds between schedule checks. Default: 15."
        ),
    )
    parser.add_argument(
        "--failure-backoff-seconds",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help=(
            "Delay after a failed due-cycle attempt. Default: 60."
        ),
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help=(
            "Stop after this many consecutive due-cycle failures. "
            "Default: 3."
        ),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help=(
            "Stop after this many due-cycle attempts. Intended for "
            "controlled testing; the normal daily controller has no "
            "cycle-count limit."
        ),
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


def _build_filter_settings(
    args: argparse.Namespace,
) -> FilterSettings:
    """Validate and construct source-neutral filter settings."""

    return FilterSettings(
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


def _print_cycle_result(
    *,
    result: WatchlistCycleResult,
    batch: Any,
    args: argparse.Namespace,
) -> None:
    """Print one due cycle consistently with the manual runner."""

    print()
    print("Watchlist controller cycle")
    print("=" * 72)
    print(f"Cycle ID         : {result.cycle_id}")
    print("Input source     : Live Schwab API")
    print(f"Request URL      : {batch.request_url}")
    print(f"HTTP status      : {batch.status_code}")
    print(f"Strategy         : {result.strategy_name}")
    print(f"Watchlist mode   : {result.watchlist_mode}")
    print(
        f"Submission       : "
        f"{'LIVE' if args.submit else 'DRY RUN'}"
    )
    print(
        f"Input candidates : "
        f"{result.pipeline_result.input_count}"
    )
    print(
        f"Accepted         : "
        f"{result.pipeline_result.accepted_count}"
    )
    print(
        f"Rejected         : "
        f"{result.pipeline_result.rejected_count}"
    )
    print(f"Status           : {result.status}")
    print()
    print("Accepted symbols:")
    print(
        " ".join(
            result.pipeline_result.accepted_symbols
        )
        or "(none)"
    )
    print()
    print(
        f"Candidate record : "
        f"{result.candidate_outputs.run_json}"
    )
    print(
        f"Cycle record     : "
        f"{result.cycle_record_path}"
    )

    if result.watchlist_plan is None:
        print("Watchlist plan   : not created")
    else:
        print(
            f"Watchlist plan   : "
            f"{result.watchlist_plan.run_record_path}"
        )


def run_due_cycle(
    *,
    client: Any,
    args: argparse.Namespace,
    filter_settings: FilterSettings,
    fetcher: Callable[..., Any] = fetch_schwab_movers,
    cycle_runner: Callable[..., WatchlistCycleResult] = (
        run_schwab_movers_cycle
    ),
    plan_applier: Callable[..., Any] = (
        apply_frozen_cycle_plan
    ),
    coordinated_applier: Callable[..., Any] = (
        apply_watchlist_plan_with_export_suspension
    ),
) -> int:
    """
    Run one due live cycle with an already-open Schwab client.

    The client is owned by the controller and is neither created nor closed
    here.
    """

    batch = fetcher(
        client,
        market=args.market,
        sort_name=args.sort,
        frequency=args.frequency,
    )

    result = cycle_runner(
        batch=batch,
        filter_settings=filter_settings,
        mode=args.mode,
        output_dir=args.output_dir,
        input_mode="api",
        root=args.root,
        wait=args.wait,
        submit_requested=args.submit,
        suppress_unchanged=(
            args.submit
            and not args.force_submit
        ),
    )

    _print_cycle_result(
        result=result,
        batch=batch,
        args=args,
    )

    if result.watchlist_plan is None:
        print(
            "No Watchlist plan was created. "
            "No command was published."
        )
        return 0

    if not args.submit:
        print("No Watchlist command was published.")
        return 0

    if result.status == CYCLE_STATUS_NO_CHANGE:
        match = result.no_change_match
        assert match is not None

        print()
        print("No Watchlist change detected.")
        print(
            f"Matches cycle     : "
            f"{match.cycle_id}"
        )
        print(
            f"Previously applied: "
            f"{match.applied_at}"
        )
        print("No Watchlist command was published.")
        return 0

    print()
    print(
        "Suspending scanner exports and applying "
        "exact frozen cycle plan..."
    )

    coordinated = coordinated_applier(
        plan_applier=plan_applier,
        plan_path=(
            result.watchlist_plan.run_record_path
        ),
        expected_cycle_id=result.cycle_id,
        output_dir=args.output_dir,
        root=args.root,
        wait=args.wait,
    )
    application = coordinated.application

    print(
        f"suspend_exports exit code : "
        f"{coordinated.suspend_command.return_code}"
    )
    print(
        f"resume_exports exit code  : "
        f"{coordinated.resume_command.return_code}"
    )

    if application.preflight is not None:
        print(
            f"Scanner preflight       : "
            f"{'READY' if application.preflight.ready else 'NOT READY'}"
        )
        print(
            f"Scanner status          : "
            f"{application.preflight.status}"
        )
        print(
            f"Scanner root            : "
            f"{application.preflight.root}"
        )

    print(
        f"mb-scan-command exit code: "
        f"{application.return_code}"
    )
    print(
        f"Application record       : "
        f"{application.run_record_path}"
    )

    if not application.successful:
        print(
            "Cycle Watchlist command was not reported as "
            "successfully processed.",
            file=sys.stderr,
        )
        return application.return_code or 1

    print(
        "Frozen cycle plan was reported as processed."
    )
    return 0


def _is_after_session(
    decision: WatchlistCycleScheduleDecision,
) -> bool:
    return (
        decision.status
        == SCHEDULE_STATUS_OUTSIDE_SESSION
        and decision.evaluated_at
        >= decision.session_end
    )


def _is_before_session(
    decision: WatchlistCycleScheduleDecision,
) -> bool:
    return (
        decision.status
        == SCHEDULE_STATUS_OUTSIDE_SESSION
        and decision.evaluated_at
        < decision.session_start
    )


def _print_schedule(
    decision: WatchlistCycleScheduleDecision,
) -> None:
    for line in (
        format_watchlist_cycle_schedule_decision(
            decision
        )
    ):
        print(line)
    print()


def _validate_args(
    args: argparse.Namespace,
) -> None:
    if args.force_submit and not args.submit:
        raise ValueError(
            "--force-submit requires --submit."
        )
    if args.timeout < 1:
        raise ValueError(
            "--timeout must be at least 1."
        )
    if args.wait < 0:
        raise ValueError(
            "--wait cannot be negative."
        )
    if args.poll_seconds <= 0:
        raise ValueError(
            "--poll-seconds must be positive."
        )
    if args.failure_backoff_seconds < 0:
        raise ValueError(
            "--failure-backoff-seconds cannot be negative."
        )
    if args.max_consecutive_failures < 1:
        raise ValueError(
            "--max-consecutive-failures must be at least 1."
        )
    if (
        args.max_cycles is not None
        and args.max_cycles < 1
    ):
        raise ValueError(
            "--max-cycles must be at least 1."
        )


def run_controller(
    args: argparse.Namespace,
    *,
    password_reader: Callable[[str], str] = (
        getpass.getpass
    ),
    client_factory: Callable[..., ClosableSchwabClient] = (
        make_secure_schwab_client
    ),
    schedule_evaluator: Callable[
        [Path],
        WatchlistCycleScheduleDecision,
    ] = evaluate_output_watchlist_cycle_schedule,
    sleep_fn: Callable[[float], None] = time.sleep,
    due_cycle_runner: Callable[..., int] = (
        run_due_cycle
    ),
    lock_factory: CycleLockFactory = (
        WatchlistCycleLock
    ),
    command: Sequence[str] = (),
) -> int:
    """Run the daily controller with injectable boundaries for tests."""

    _validate_args(args)
    filter_settings = _build_filter_settings(args)

    resolved_output_dir = (
        args.output_dir
        .expanduser()
        .resolve(strict=False)
    )

    initial_decision = schedule_evaluator(
        resolved_output_dir
    )
    _print_schedule(initial_decision)

    if (
        initial_decision.status
        == SCHEDULE_STATUS_NON_TRADING_DAY
    ):
        print(
            "No controller was started because the "
            "schedule does not run today."
        )
        return 0

    if _is_after_session(initial_decision):
        print(
            "No controller was started because the "
            "market session has ended."
        )
        return 0

    ecfg_path = resolve_ecfg_path(
        args.ecfg
    ).resolve(strict=False)

    if not ecfg_path.is_file():
        raise ValueError(
            "Schwab encrypted configuration does not "
            f"exist: {ecfg_path}"
        )

    lock_path = (
        args.lock_file
        .expanduser()
        .resolve(strict=False)
        if args.lock_file is not None
        else default_watchlist_cycle_lock_path(
            resolved_output_dir
        )
    )

    effective_command = tuple(command) or (
        sys.executable,
        str(Path(__file__).resolve()),
    )

    client: ClosableSchwabClient | None = None

    with lock_factory(
        lock_path,
        command=effective_command,
    ):
        password = password_reader(
            "ecfg password: "
        )

        try:
            client = client_factory(
                ecfg_path,
                password,
                timeout=args.timeout,
            )
        finally:
            # Python cannot guarantee in-place erasure of immutable strings,
            # but the controller retains no intentional password reference.
            password = ""

        print("Watchlist daily controller")
        print("=" * 72)
        print(f"Encrypted config : {ecfg_path}")
        print(f"Output directory : {resolved_output_dir}")
        print(f"Poll interval    : {args.poll_seconds} seconds")
        print(
            f"Submission       : "
            f"{'LIVE' if args.submit else 'DRY RUN'}"
        )
        print(
            "Press Ctrl+C to stop the controller."
        )
        print()

        decision = initial_decision
        attempted_cycles = 0
        consecutive_failures = 0
        last_display_key: tuple[Any, ...] | None = None

        try:
            while True:
                display_key = (
                    decision.status,
                    decision.phase,
                    decision.last_cycle_id,
                    decision.next_due_at,
                )

                if display_key != last_display_key:
                    _print_schedule(decision)
                    last_display_key = display_key

                if (
                    decision.status
                    == SCHEDULE_STATUS_NON_TRADING_DAY
                ):
                    print(
                        "Controller stopping: the schedule "
                        "does not run today."
                    )
                    return 0

                if _is_after_session(decision):
                    print(
                        "Controller stopping: the market "
                        "session has ended."
                    )
                    return 0

                if decision.due:
                    attempted_cycles += 1

                    try:
                        exit_code = due_cycle_runner(
                            client=client,
                            args=args,
                            filter_settings=filter_settings,
                        )
                    except Exception as exc:
                        consecutive_failures += 1
                        print(
                            "ERROR: Due Watchlist cycle "
                            f"failed: {type(exc).__name__}: "
                            f"{exc}",
                            file=sys.stderr,
                        )

                        if (
                            consecutive_failures
                            >= args.max_consecutive_failures
                        ):
                            print(
                                "Controller stopping after "
                                f"{consecutive_failures} "
                                "consecutive failures.",
                                file=sys.stderr,
                            )
                            return 1

                        if (
                            args.failure_backoff_seconds
                            > 0
                        ):
                            sleep_fn(
                                args.failure_backoff_seconds
                            )
                    else:
                        if exit_code != 0:
                            consecutive_failures += 1

                            if (
                                consecutive_failures
                                >= args.max_consecutive_failures
                            ):
                                print(
                                    "Controller stopping after "
                                    f"{consecutive_failures} "
                                    "consecutive failed cycle "
                                    "results.",
                                    file=sys.stderr,
                                )
                                return exit_code
                        else:
                            consecutive_failures = 0

                    if (
                        args.max_cycles is not None
                        and attempted_cycles
                        >= args.max_cycles
                    ):
                        print(
                            "Controller stopping after the "
                            "requested cycle-count limit."
                        )
                        return 0

                if _is_before_session(decision):
                    print(
                        "Controller is waiting for the "
                        "market session to begin."
                    )

                sleep_fn(args.poll_seconds)
                decision = schedule_evaluator(
                    resolved_output_dir
                )
        except KeyboardInterrupt:
            print()
            print(
                "Controller stopped by the operator."
            )
            return 0
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Command-line entry point."""

    raw_argv = list(
        sys.argv[1:]
        if argv is None
        else argv
    )

    try:
        args = build_parser().parse_args(raw_argv)
        return run_controller(
            args,
            command=(
                sys.executable,
                str(Path(__file__).resolve()),
                *raw_argv,
            ),
        )
    except WatchlistCycleLockHeldError as exc:
        print(
            "ERROR: Another Watchlist controller or "
            "scheduled cycle already holds the "
            "execution lock.",
            file=sys.stderr,
        )
        print(
            f"Lock file        : {exc.lock_path}",
            file=sys.stderr,
        )
        print(
            "No Watchlist controller was started.",
            file=sys.stderr,
        )
        return LOCK_HELD_EXIT_CODE
    except SchwabdevNotInstalledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except SecureSchwabConfigError as exc:
        print(
            f"ERROR: Invalid Schwab configuration: "
            f"{exc}",
            file=sys.stderr,
        )
        return 4
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"ERROR: Watchlist controller failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
