"""Run one manually initiated, dry-run Watchlist cycle."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
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
    CYCLE_STATUS_PLAN_CREATED,
    run_schwab_movers_cycle,
)
from watchlist_submission import COMMAND_FOR_MODE

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
            "Run one manual, dry-run Watchlist cycle from either the live "
            "Schwab Movers endpoint or a saved response. The command creates "
            "candidate outputs, a frozen Watchlist plan, and a cycle audit "
            "record. It never publishes a live scanner command."
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

    replay_path: Path | None = None

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
