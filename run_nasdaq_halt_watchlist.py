from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
import uuid

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mb_market_data.nasdaq_halts import fetch_trade_halts
from mb_market_data.nasdaq_halt_monitor import NasdaqHaltMonitor

from scanner_export_coordination import (
    run_scanner_control_command,
)
from scanner_preflight import (
    ScannerPreflightResult,
    check_scanner_ready,
)
from watchlist_submission import (
    RECORD_ORIGIN_DIRECT_SUBMISSION,
    WatchlistSubmissionResult,
    build_watchlist_command,
    submit_watchlist_symbols,
)


ET_ZONE = ZoneInfo("America/New_York")

DEFAULT_POLL_SECONDS = 60
DEFAULT_FETCH_TIMEOUT = 60.0
DEFAULT_SCANNER_WAIT = 30.0
DEFAULT_SCANNER_STATE_WAIT = 45.0
DEFAULT_STATE_POLL_SECONDS = 0.25
DEFAULT_VERIFY_WAIT = 45.0


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


def resolve_verification_dir(
    explicit_dir: Path | None,
) -> Path:
    """
    Resolve the MasterBot-local Watchlist verification directory.

    By default this is:

        %MB_SCANS%\\watchlist_verify
    """
    if explicit_dir is not None:
        return explicit_dir.expanduser().resolve()

    configured_scans = os.environ.get(
        "MB_SCANS",
        "",
    ).strip()

    if not configured_scans:
        raise RuntimeError(
            "MB_SCANS is not set and no "
            "--verification-dir was supplied."
        )

    scans_dir = Path(
        os.path.expandvars(configured_scans)
    ).expanduser().resolve()

    return scans_dir / "watchlist_verify"


def build_verification_filename(
    poll_time: datetime,
) -> str:
    """
    Build a unique, known filename for one verification export.
    """
    timestamp = poll_time.astimezone(
        ET_ZONE
    ).strftime(
        "%Y-%m-%d-%H-%M-%S"
    )

    unique_suffix = uuid.uuid4().hex[:8]

    return (
        f"{timestamp}-NASDAQ-HALT-"
        f"{unique_suffix}-WL.csv"
    )


def build_verification_export_command(
    *,
    target_filename: str,
    root: Path | None,
    wait: float,
    executable: str,
) -> tuple[str, ...]:
    if wait <= 0:
        raise ValueError(
            "Verification export requires "
            "a positive scanner wait."
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
                str(
                    root.expanduser().resolve()
                ),
            ]
        )

    command.extend(
        [
            "--wait",
            str(wait),
        ]
    )

    return tuple(command)


def run_verification_export(
    *,
    target_filename: str,
    root: Path | None,
    wait: float,
) -> tuple[tuple[str, ...], int]:
    executable = shutil.which(
        "mb-scan-command"
    )

    if executable is None:
        raise RuntimeError(
            "mb-scan-command was not found on PATH."
        )

    command = build_verification_export_command(
        target_filename=target_filename,
        root=root,
        wait=wait,
        executable=executable,
    )

    try:
        completed = subprocess.run(
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
) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if path.exists() and path.is_file():
            return True

        time.sleep(0.25)

    return path.exists() and path.is_file()


def scanner_state_matches(
    result: ScannerPreflightResult,
    *,
    expect_suspended: bool,
) -> bool:
    if expect_suspended:
        return (
            result.ready
            and result.status == "HEALTHY"
            and result.loop_state
            == "exports_suspended"
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
) -> ScannerPreflightResult:
    if timeout <= 0:
        raise ValueError(
            "Scanner state wait must be positive."
        )

    deadline = time.monotonic() + timeout

    while True:
        result = check_scanner_ready(
            root=root,
            allow_exports_suspended=(
                expect_suspended
            ),
        )

        if scanner_state_matches(
            result,
            expect_suspended=expect_suspended,
        ):
            return result

        if time.monotonic() >= deadline:
            return result

        time.sleep(
            DEFAULT_STATE_POLL_SECONDS
        )


def read_watchlist_symbols(
    path: Path,
) -> set[str]:
    """
    Read symbols from a ThinkOrSwim Watchlist export.

    The ToS file contains preamble rows before the CSV header,
    so locate the row whose first column is 'Symbol'.
    """
    symbols: set[str] = set()
    header_found = False

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as input_file:
        reader = csv.reader(input_file)

        for row in reader:
            if not row:
                continue

            first_column = row[0].strip()

            if not header_found:
                if first_column == "Symbol":
                    header_found = True

                continue

            if first_column:
                symbols.add(
                    first_column.upper()
                )

    if not header_found:
        raise RuntimeError(
            "Watchlist verification CSV does not "
            f"contain a Symbol header: {path}"
        )

    return symbols


def submit_and_verify_live(
    *,
    symbols: list[str],
    poll_time: datetime,
    wait: float,
    root: Path | None,
    output_dir: Path,
    verification_dir: Path,
) -> tuple[
    WatchlistSubmissionResult,
    Path,
]:
    """
    Perform one protected, closed-loop Watchlist addition.

    Sequence:

        preflight
        suspend scheduled exports
        verify suspended state
        add symbols
        explicitly export WL
        verify exported symbols
        resume scheduled exports
        verify active state

    The caller should mark Nasdaq symbols seen only after this
    function returns successfully.
    """
    preflight_before = check_scanner_ready(
        root=root,
    )

    if not preflight_before.ready:
        raise RuntimeError(
            "Scanner preflight before export "
            "suspension failed: "
            f"{preflight_before.detail}"
        )

    verification_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_filename = (
        build_verification_filename(
            poll_time
        )
    )

    verification_path = (
        verification_dir
        / target_filename
    )

    if verification_path.exists():
        raise RuntimeError(
            "Refusing to reuse an existing "
            "verification file: "
            f"{verification_path}"
        )

    suspend_attempted = False

    try:
        suspend_attempted = True

        suspend_result = (
            run_scanner_control_command(
                action="suspend_exports",
                root=root,
                wait=wait,
            )
        )

        if not suspend_result.successful:
            raise RuntimeError(
                "suspend_exports was not "
                "reported as successful; "
                f"exit code="
                f"{suspend_result.return_code}."
            )

        suspended_preflight = (
            wait_for_scanner_state(
                root=root,
                expect_suspended=True,
                timeout=(
                    DEFAULT_SCANNER_STATE_WAIT
                ),
            )
        )

        if not scanner_state_matches(
            suspended_preflight,
            expect_suspended=True,
        ):
            raise RuntimeError(
                "Scanner did not enter the "
                "expected suspended-export state "
                "within the allowed wait: "
                f"{suspended_preflight.detail}"
            )


        def suspended_submission_preflight(
            *,
            root: Path | None,
        ) -> ScannerPreflightResult:
            result = check_scanner_ready(
                root=root,
                allow_exports_suspended=True,
            )

            if not scanner_state_matches(
                result,
                expect_suspended=True,
            ):
                raise RuntimeError(
                    "Scanner left the expected "
                    "suspended-export state before "
                    "Watchlist submission: "
                    f"{result.detail}"
                )

            return result

        submission = submit_watchlist_symbols(
            mode="add",
            symbols=symbols,
            submit=True,
            wait=wait,
            root=root,
            output_dir=output_dir,
            record_origin=(
                RECORD_ORIGIN_DIRECT_SUBMISSION
            ),
            preflight_checker=(
                suspended_submission_preflight
            ),
        )

        if not (
            submission.submitted
            and submission.successful
        ):
            raise RuntimeError(
                "add_wl_symbols was not "
                "reported as successful; "
                f"exit code="
                f"{submission.return_code}."
            )

        _, export_return_code = (
            run_verification_export(
                target_filename=(
                    target_filename
                ),
                root=root,
                wait=wait,
            )
        )

        if export_return_code != 0:
            raise RuntimeError(
                "Verification export_wl was "
                "not reported as successful; "
                f"exit code="
                f"{export_return_code}."
            )

        if not wait_for_file(
            verification_path,
            timeout=DEFAULT_VERIFY_WAIT,
        ):
            raise RuntimeError(
                "Verification Watchlist CSV "
                "did not appear on MasterBot: "
                f"{verification_path}"
            )

        exported_symbols = (
            read_watchlist_symbols(
                verification_path
            )
        )

        missing_symbols = [
            symbol
            for symbol in symbols
            if symbol.upper()
            not in exported_symbols
        ]

        if missing_symbols:
            raise RuntimeError(
                "Verification failed; "
                "Watchlist export is missing: "
                + " ".join(
                    missing_symbols
                )
            )

    finally:
        if suspend_attempted:
            resume_result = (
                run_scanner_control_command(
                    action="resume_exports",
                    root=root,
                    wait=wait,
                )
            )

            if not resume_result.successful:
                raise RuntimeError(
                    "resume_exports was not "
                    "reported as successful; "
                    f"exit code="
                    f"{resume_result.return_code}. "
                    "Scanner exports may remain "
                    "suspended."
                )

            resumed_preflight = (
                wait_for_scanner_state(
                    root=root,
                    expect_suspended=False,
                    timeout=(
                        DEFAULT_SCANNER_STATE_WAIT
                    ),
                )
            )

            if not scanner_state_matches(
                resumed_preflight,
                expect_suspended=False,
            ):
                raise RuntimeError(
                    "Scanner did not return to "
                    "the expected active-export "
                    "state after resume_exports "
                    "within the allowed wait: "
                    f"{resumed_preflight.detail}"
                )

    return submission, verification_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Monitor Nasdaq volatility halts and "
            "optionally add new symbols to the "
            "ThinkOrSwim Watchlist."
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
            "mb-scan-command processing wait. "
            "Default: 30."
        ),
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root. If omitted, "
            "MB_SCAN_CONTROL is used."
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
        help=(
            "Directory for Watchlist run records."
        ),
    )

    parser.add_argument(
        "--verification-dir",
        type=Path,
        default=None,
        help=(
            "MasterBot-local directory containing "
            "explicit Watchlist verification exports. "
            "Default: %%MB_SCANS%%\\watchlist_verify."
        ),
    )

    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "Perform the live protected "
            "add/export/verify transaction. "
            "Without this option, only perform "
            "a dry run."
        ),
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
            "ERROR: --interval must be at least "
            "60 seconds.",
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

    if args.submit and args.wait <= 0:
        print(
            "ERROR: live submission requires "
            "--wait greater than zero.",
            file=sys.stderr,
        )
        return 2

    if args.submit and args.date is not None:
        print(
            "ERROR: --submit cannot be used with "
            "--date. Historical replay is "
            "dry-run only.",
            file=sys.stderr,
        )
        return 2

    verification_dir: Path | None = None

    if args.submit:
        try:
            verification_dir = (
                resolve_verification_dir(
                    args.verification_dir
                )
            )

        except RuntimeError as exc:
            print(
                f"ERROR: {exc}",
                file=sys.stderr,
            )
            return 2

    monitor = NasdaqHaltMonitor()

    # Historical replay keeps its existing behavior.
    # In CURRENT mode, the first successful feed retrieval
    # establishes a baseline of already-halted symbols and
    # performs no Watchlist mutation.
    baseline_established = (
        args.date is not None
    )

    mode_name = (
        "CURRENT"
        if args.date is None
        else "HISTORICAL REPLAY"
    )

    run_mode = (
        "LIVE CLOSED LOOP"
        if args.submit
        else "DRY RUN"
    )

    print(
        "Nasdaq Halt -> Watchlist "
        f"{run_mode}"
    )
    print("=" * 70)
    print(f"Mode           : {mode_name}")

    if args.date is not None:
        print(
            f"Replay date    : "
            f"{args.date.isoformat()}"
        )

    print(f"Polls          : {args.polls}")
    print(
        f"Poll interval  : "
        f"{args.interval:g} seconds"
    )
    print(
        f"Fetch timeout  : "
        f"{args.timeout:g} seconds"
    )
    print("Reason codes   : LUDP, M")
    print("Watchlist mode : add")
    print(
        f"Submission     : "
        f"{'LIVE' if args.submit else 'DRY RUN'}"
    )

    if verification_dir is not None:
        print(
            f"Verify dir     : "
            f"{verification_dir}"
        )

    print()

    for poll_number in range(
        1,
        args.polls + 1,
    ):
        poll_time = now_et()

        session_date = (
            poll_time.date()
            if args.date is None
            else args.date
        )

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
            new_symbols = (
                monitor.pending_symbols(
                    feed.records,
                    session_date=session_date,
                )
            )

            print(
                f"  Feed mode    : "
                f"{feed.retrieval_mode}"
            )

            print(
                f"  Feed records : "
                f"{len(feed.records)}"
            )

            if not baseline_established:
                baseline_symbols = list(
                    new_symbols
                )

                if baseline_symbols:
                    monitor.mark_seen(
                        baseline_symbols,
                        session_date=session_date,
                    )

                baseline_established = True

                print(
                    f"  Seen symbols : "
                    f"{len(monitor.seen_symbols)}"
                )

                print(
                    "  New symbols  : 0"
                )

                print(
                    f"  Baseline     : "
                    f"{len(baseline_symbols)} "
                    "existing halt symbol(s)"
                )

                if baseline_symbols:
                    print(
                        "  Baseline syms: "
                        + " ".join(
                            baseline_symbols
                        )
                    )

                print(
                    "  Action       : startup "
                    "baseline only; no Watchlist "
                    "submission"
                )

                # Prevent the normal submission path
                # from acting on startup backlog.
                new_symbols = []

            else:
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

                preview_command = (
                    build_watchlist_command(
                        mode="add",
                        symbols=new_symbols,
                        wait=args.wait,
                        root=args.root,
                    )
                )

                print(
                    "  Command      : "
                    + " ".join(
                        str(part)
                        for part
                        in preview_command
                    )
                )

                if not args.submit:
                    try:
                        result = (
                            submit_watchlist_symbols(
                                mode="add",
                                symbols=new_symbols,
                                submit=False,
                                wait=args.wait,
                                root=args.root,
                                output_dir=(
                                    args.output_dir
                                ),
                                record_origin=(
                                    RECORD_ORIGIN_DIRECT_SUBMISSION
                                ),
                            )
                        )

                    except (
                        ValueError,
                        RuntimeError,
                    ) as exc:
                        print(
                            f"  Dry-run ERROR: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )

                    else:
                        print(
                            f"  Run record   : "
                            f"{result.run_record_path}"
                        )

                        monitor.mark_seen(
                            new_symbols,
                            session_date=(
                                session_date
                            ),
                        )

                        print(
                            "  Published    : NO"
                        )

                else:
                    assert (
                        verification_dir
                        is not None
                    )

                    try:
                        (
                            result,
                            verification_path,
                        ) = submit_and_verify_live(
                            symbols=new_symbols,
                            poll_time=poll_time,
                            wait=args.wait,
                            root=args.root,
                            output_dir=(
                                args.output_dir
                            ),
                            verification_dir=(
                                verification_dir
                            ),
                        )

                    except (
                        ValueError,
                        RuntimeError,
                    ) as exc:
                        print(
                            f"  Transaction ERROR: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )

                        print(
                            "  Published    : NO"
                        )

                        print(
                            "  Symbols remain pending "
                            "for the next poll."
                        )

                    else:
                        print(
                            f"  Run record   : "
                            f"{result.run_record_path}"
                        )

                        print(
                            f"  Exit code    : "
                            f"{result.return_code}"
                        )

                        print(
                            f"  Verification : "
                            f"{verification_path}"
                        )

                        print(
                            "  Verified     : YES"
                        )

                        # Mark seen only after:
                        #   add succeeded,
                        #   verification succeeded,
                        #   exports resumed successfully,
                        #   active scanner state was verified.
                        monitor.mark_seen(
                            new_symbols,
                            session_date=(
                                session_date
                            ),
                        )

                        print(
                            "  Published    : YES"
                        )

        print()

        if poll_number < args.polls:
            time.sleep(
                args.interval
            )

    print(
        f"{run_mode.title()} complete."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
