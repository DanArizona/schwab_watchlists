from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import (
    SecureSchwabConfigError,
)
from mb_market_data.nasdaq_halt_monitor import (
    NasdaqHaltMonitor,
)
from mb_market_data.nasdaq_halts import (
    fetch_trade_halts,
)
from mb_watchlist_coordinator.coordinator import (
    WatchlistCoordinator,
)
from mb_watchlist_coordinator.models import (
    IntentType,
    ProducerIntent,
)

from scanner_export_coordination import (
    run_scanner_control_command,
)
from scanner_preflight import (
    check_scanner_ready,
)
from watchlist_submission import (
    submit_watchlist_symbols,
)

from schwab_watchlists.ludp_coordinator import (
    build_ludp_intent,
    reconcile_tos_until_stable,
)
from schwab_watchlists.ov_coordinator import (
    acquire_live_ov_batch,
    build_ov_base_intent,
)
from schwab_watchlists.tos_coordinator_executor import (
    LiveToSExecutor,
)
from schwab_watchlists.tos_watchlist_transport import (
    read_watchlist_symbols,
)


EASTERN = ZoneInfo("America/New_York")

DEFAULT_POLL_SECONDS = 60.0
DEFAULT_FETCH_TIMEOUT = 60.0
DEFAULT_SCANNER_WAIT = 30.0
DEFAULT_SCHWAB_TIMEOUT = 10
DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"


@dataclass(slots=True)
class LudpPollingState:
    """
    State owned by the Nasdaq producer for one ET session.

    accepted_ludp_symbols means an ENSURE_PRESENT intent has already
    been accepted by the coordinator. It does NOT mean downstream ToS
    handling has succeeded.
    """

    session_date: date
    baseline_established: bool = False
    accepted_ludp_symbols: set[str] = field(
        default_factory=set
    )


def now_et() -> datetime:
    return datetime.now(EASTERN)


def build_transaction_id() -> str:
    now = now_et()

    return (
        "T-LUDP-"
        + now.strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:8]
    )


def build_ludp_intent_id() -> str:
    now = now_et()

    return (
        "ludp-"
        + now.strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:8]
    )


def build_ov_intent_id() -> str:
    now = now_et()

    return (
        "ov-"
        + now.strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:8]
    )


def build_baseline_intent(
    symbols: frozenset[str],
    *,
    created_at: datetime,
    baseline_path: Path,
) -> ProducerIntent:
    if not symbols:
        raise ValueError(
            "Baseline Watchlist cannot be empty."
        )

    return ProducerIntent(
        intent_id=(
            "baseline-"
            + created_at.astimezone(
                EASTERN
            ).strftime(
                "%Y%m%d-%H%M%S"
            )
            + "-"
            + uuid4().hex[:8]
        ),
        producer_id="bootstrap-baseline",
        intent_type=IntentType.BASE_SET,
        symbols=symbols,
        created_at=created_at,
        reason=(
            "Bootstrap baseline for live "
            "LUDP coordinator"
        ),
        metadata={
            "baseline_path": str(
                baseline_path
            ),
        },
    )


def process_ludp_poll(
    *,
    coordinator: WatchlistCoordinator,
    executor: LiveToSExecutor,
    monitor: NasdaqHaltMonitor,
    state: LudpPollingState,
    records,
    poll_time: datetime,
) -> tuple[str, ...]:
    """
    Process one successful Nasdaq feed retrieval.

    First successful retrieval establishes the Nasdaq startup baseline
    and performs no LUDP mutation.

    Later pending halt symbols receive ENSURE_PRESENT intent. They are
    acknowledged by NasdaqHaltMonitor only after ToS reaches stable
    NO_OP reconciliation.
    """

    session_date = (
        poll_time.astimezone(
            EASTERN
        ).date()
    )

    if session_date != state.session_date:
        raise RuntimeError(
            "ET session date changed while "
            "LUDP coordinator was running."
        )

    pending = monitor.pending_symbols(
        records,
        session_date=session_date,
    )

    if not state.baseline_established:
        if pending:
            monitor.mark_seen(
                pending,
                session_date=session_date,
            )

        state.baseline_established = True

        return ()

    #
    # If a previous reconciliation failed, those symbols remain
    # pending in NasdaqHaltMonitor. Do not create duplicate coordinator
    # intents for them; simply retry reconciliation.
    #
    new_intent_symbols = tuple(
        symbol
        for symbol in pending
        if symbol
        not in state.accepted_ludp_symbols
    )

    if new_intent_symbols:
        intent = build_ludp_intent(
            new_intent_symbols,
            intent_id=(
                build_ludp_intent_id()
            ),
            created_at=poll_time,
            session_date=session_date,
        )

        coordinator.accept_intent(
            intent,
            at=poll_time,
        )

        state.accepted_ludp_symbols.update(
            new_intent_symbols
        )

    if pending:
        reconcile_tos_until_stable(
            coordinator,
            executor,
            transaction_id_factory=(
                build_transaction_id
            ),
            now_factory=now_et,
        )

        #
        # Acknowledge only after stable coordinator/ToS reconciliation.
        #
        monitor.mark_seen(
            pending,
            session_date=session_date,
        )

    return tuple(pending)


def resolve_verification_dir() -> Path:
    scans = os.environ.get(
        "MB_SCANS",
        "",
    ).strip()

    if not scans:
        raise RuntimeError(
            "MB_SCANS is not set."
        )

    return (
        Path(
            os.path.expandvars(
                scans
            )
        ).expanduser()
        / "watchlist_verify"
    )


def resolve_verification_outbox_dir() -> Path:
    control = os.environ.get(
        "MB_SCAN_CONTROL",
        "",
    ).strip()

    if not control:
        raise RuntimeError(
            "MB_SCAN_CONTROL is not set."
        )

    return (
        Path(
            os.path.expandvars(
                control
            )
        ).expanduser()
        / "outgoing"
        / "watchlist_verify"
    )


def resolve_ecfg_path(
    explicit_path: Path | None,
) -> Path:
    if explicit_path is not None:
        return explicit_path.expanduser()

    schwab_ecfg = os.environ.get(
        "MB_SCHWAB_ECFG"
    )

    if schwab_ecfg:
        return Path(
            schwab_ecfg
        ).expanduser()

    vault = os.environ.get(
        "MB_VAULT"
    )

    if vault:
        return (
            Path(vault).expanduser()
            / DEFAULT_ECFG_NAME
        )

    return Path(
        DEFAULT_ECFG_NAME
    )


def determine_startup_mode(
    args: argparse.Namespace,
) -> str:
    has_baseline = (
        args.baseline is not None
    )

    has_ov = (
        args.ov_watchlist is not None
    )

    if has_baseline == has_ov:
        raise ValueError(
            "Specify exactly one startup source: "
            "either baseline CSV or --ov-watchlist."
        )

    if has_ov:
        if (
            args.ov_limit is None
            or args.ov_limit <= 0
        ):
            raise ValueError(
                "--ov-limit must be positive "
                "when --ov-watchlist is used."
            )

        return "ov"

    return "baseline"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MasterBot coordinator with "
            "Nasdaq LUDP/M ENSURE_PRESENT intents."
        )
    )

    parser.add_argument(
        "baseline",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Optional authoritative bootstrap Watchlist CSV. "
            "Omit when using --ov-watchlist."
        ),
    )

    parser.add_argument(
        "--ov-watchlist",
        type=Path,
        default=None,
        help=(
            "Same-day ToS Watchlist CSV containing OV_DECISION. "
            "Uses live Schwab quotes to build the initial BASE_SET."
        ),
    )

    parser.add_argument(
        "--ov-limit",
        type=int,
        default=None,
        help=(
            "Maximum number of Overnight Volume symbols "
            "to include in BASE_SET."
        ),
    )

    parser.add_argument(
        "--ecfg",
        type=Path,
        default=None,
        help=(
            "Encrypted Schwab configuration. Defaults to "
            "MB_SCHWAB_ECFG, then "
            "MB_VAULT\\secure_schwabdev.ecfg, "
            "then the current directory."
        ),
    )

    parser.add_argument(
        "--schwab-timeout",
        type=int,
        default=DEFAULT_SCHWAB_TIMEOUT,
        help=(
            "Schwab client request timeout in seconds. "
            "Default: 10."
        ),
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Enable live ToS reconciliation. "
            "Required for this initial production runner."
        ),
    )

    parser.add_argument(
        "--polls",
        type=int,
        default=1,
        help=(
            "Nasdaq poll count. "
            "Use 0 for continuous operation."
        ),
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Seconds between Nasdaq polls.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_FETCH_TIMEOUT,
        help="Nasdaq fetch timeout.",
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_SCANNER_WAIT,
        help="mb-scan-command processing wait.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "output"
            / "ludp_coordinator"
        ),
        help="Watchlist submission run records.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        startup_mode = (
            determine_startup_mode(
                args
            )
        )
    except ValueError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    if not args.live:
        print(
            "ERROR: --live is required.",
            file=sys.stderr,
        )
        return 2

    if args.polls < 0:
        print(
            "ERROR: --polls cannot be negative.",
            file=sys.stderr,
        )
        return 2

    if args.interval < 60:
        print(
            "ERROR: --interval must be at "
            "least 60 seconds.",
            file=sys.stderr,
        )
        return 2

    if args.timeout <= 0:
        print(
            "ERROR: --timeout must be positive.",
            file=sys.stderr,
        )
        return 2

    if args.schwab_timeout < 1:
        print(
            "ERROR: --schwab-timeout "
            "must be at least 1.",
            file=sys.stderr,
        )
        return 2

    if args.wait <= 0:
        print(
            "ERROR: --wait must be positive.",
            file=sys.stderr,
        )
        return 2

    try:
        verification_dir = (
            resolve_verification_dir()
        )

        verification_outbox_dir = (
            resolve_verification_outbox_dir()
        )

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    start_time = now_et()
    session_date = start_time.date()

    coordinator = WatchlistCoordinator()

    startup_description: str
    startup_symbol_count: int
    startup_ranked_symbols: tuple[
        str,
        ...
    ] | None = None

    if startup_mode == "baseline":
        assert args.baseline is not None

        baseline_path = (
            args.baseline
            .expanduser()
            .resolve()
        )

        if not baseline_path.is_file():
            print(
                "ERROR: baseline file does not exist: "
                f"{baseline_path}",
                file=sys.stderr,
            )
            return 2

        try:
            baseline_symbols = frozenset(
                read_watchlist_symbols(
                    baseline_path
                )
            )

        except Exception as exc:
            print(
                f"ERROR: {exc}",
                file=sys.stderr,
            )
            return 2

        startup_intent = (
            build_baseline_intent(
                baseline_symbols,
                created_at=start_time,
                baseline_path=(
                    baseline_path
                ),
            )
        )

        startup_description = (
            f"Baseline CSV: {baseline_path}"
        )

        startup_symbol_count = len(
            baseline_symbols
        )

    else:
        assert args.ov_watchlist is not None
        assert args.ov_limit is not None

        ov_watchlist_path = (
            args.ov_watchlist
            .expanduser()
            .resolve()
        )

        if not ov_watchlist_path.is_file():
            print(
                "ERROR: OV Watchlist file "
                "does not exist: "
                f"{ov_watchlist_path}",
                file=sys.stderr,
            )
            return 2

        ecfg_path = (
            resolve_ecfg_path(
                args.ecfg
            ).resolve()
        )

        if not ecfg_path.is_file():
            print(
                "ERROR: Schwab encrypted "
                "configuration does not exist: "
                f"{ecfg_path}",
                file=sys.stderr,
            )
            return 2

        client = None

        try:
            password = getpass.getpass(
                "ecfg password: "
            )

            client = (
                make_secure_schwab_client(
                    ecfg_path,
                    password,
                    timeout=(
                        args.schwab_timeout
                    ),
                )
            )

            ov_batch = acquire_live_ov_batch(
                client,
                ov_watchlist_path,
                trade_date=session_date,
            )

            intent_time = now_et()

            startup_intent = (
                build_ov_base_intent(
                    ov_batch,
                    limit=args.ov_limit,
                    intent_id=(
                        build_ov_intent_id()
                    ),
                    created_at=(
                        intent_time
                    ),
                )
            )

        except SchwabdevNotInstalledError as exc:
            print(
                f"ERROR: {exc}",
                file=sys.stderr,
            )
            return 3

        except SecureSchwabConfigError as exc:
            print(
                "ERROR: Invalid Schwab "
                f"configuration: {exc}",
                file=sys.stderr,
            )
            return 4

        except Exception as exc:
            print(
                "ERROR: OV acquisition failed: "
                f"{type(exc).__name__}: "
                f"{exc}",
                file=sys.stderr,
            )
            return 1

        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        startup_ranked_symbols = tuple(
            startup_intent.metadata[
                "ranked_symbols"
            ]
        )

        startup_description = (
            f"Overnight Volume: "
            f"{ov_watchlist_path}"
        )

        startup_symbol_count = len(
            startup_intent.symbols
        )

    coordinator.accept_intent(
        startup_intent,
        at=startup_intent.created_at,
    )

    executor = LiveToSExecutor(
        root=None,
        verification_dir=(
            verification_dir
        ),
        verification_outbox_dir=(
            verification_outbox_dir
        ),
        wait=args.wait,
        preflight_checker=(
            check_scanner_ready
        ),
        control_executor=(
            run_scanner_control_command
        ),
        output_dir=args.output_dir,
        submitter=(
            submit_watchlist_symbols
        ),
    )

    print(
        "MasterBot OV + LUDP Coordinator"
    )

    print("=" * 70)
    print(
        f"Session date : "
        f"{session_date.isoformat()} ET"
    )
    print(
        f"Base producer: "
        f"{startup_description}"
    )

    print(
        f"BASE_SET syms: "
        f"{startup_symbol_count}"
    )

    if startup_ranked_symbols is not None:
        print(
            "OV ranking   : "
            + " ".join(
                startup_ranked_symbols
            )
        )

    print(
        f"Verify dir   : "
        f"{verification_dir}"
    )
    print(
        f"Outbox       : "
        f"{verification_outbox_dir}"
    )
    print(
        f"Poll interval: "
        f"{args.interval:g} seconds"
    )
    print(
        "Reason codes : LUDP, M"
    )
    print()
    #
    # First make ToS agree with the authoritative baseline.
    #
    print(
        "Reconciling startup BASE_SET..."
    )

    try:
        steps = reconcile_tos_until_stable(
            coordinator,
            executor,
            transaction_id_factory=(
                build_transaction_id
            ),
            now_factory=now_et,
        )

    except Exception as exc:
        print(
            "ERROR: BASE_SET reconciliation "
            f"failed: {type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "BASE_SET stable : YES "
        f"({len(steps)} step(s))"
    )
    print()

    monitor = NasdaqHaltMonitor()

    state = LudpPollingState(
        session_date=session_date,
    )

    poll_number = 0

    try:
        while (
            args.polls == 0
            or poll_number < args.polls
        ):
            poll_number += 1

            poll_time = now_et()

            if poll_time.date() != session_date:
                print(
                    "ET session date changed; "
                    "stopping daily runner."
                )
                return 0

            print(
                f"[{poll_time.strftime('%Y-%m-%d %H:%M:%S ET')}] "
                f"Poll {poll_number}"
            )

            try:
                feed = fetch_trade_halts(
                    timeout=args.timeout,
                )

            except Exception as exc:
                print(
                    "  Nasdaq fetch ERROR: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

            else:
                was_baseline = (
                    not state.baseline_established
                )

                try:
                    handled = process_ludp_poll(
                        coordinator=coordinator,
                        executor=executor,
                        monitor=monitor,
                        state=state,
                        records=feed.records,
                        poll_time=poll_time,
                    )

                except Exception as exc:
                    print(
                        "  Reconciliation ERROR: "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

                    print(
                        "  Halt symbols remain "
                        "pending for next poll."
                    )

                else:
                    if was_baseline:
                        print(
                            "  Nasdaq startup baseline "
                            "established."
                        )
                        print(
                            f"  Existing halt symbols: "
                            f"{len(monitor.seen_symbols)}"
                        )

                    elif handled:
                        print(
                            "  LUDP handled : "
                            + " ".join(
                                handled
                            )
                        )

                        print(
                            f"  Canonical rev: "
                            f"{coordinator.current_canonical.revision}"
                        )

                    else:
                        print(
                            "  New LUDP     : 0"
                        )

            print()

            if (
                args.polls == 0
                or poll_number < args.polls
            ):
                time.sleep(
                    args.interval
                )

    except KeyboardInterrupt:
        print()
        print(
            "LUDP coordinator stopped."
        )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
