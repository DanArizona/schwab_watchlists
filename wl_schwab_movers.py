"""
Retrieve market movers from the Charles Schwab API.

This first version is API-only. It does not submit symbols to ThinkOrSwim.

Examples
--------
NASDAQ percentage gainers:

    python wl_schwab_movers.py

NYSE percentage losers:

    python wl_schwab_movers.py ^
        --market NYSE ^
        --sort PERCENT_CHANGE_DOWN

Request NASDAQ symbols ordered by volume:

    python wl_schwab_movers.py ^
        --market NASDAQ ^
        --sort VOLUME ^
        --frequency 5

Limit the displayed and saved symbol list:

    python wl_schwab_movers.py --limit 20
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import SecureSchwabConfigError
from candidate_filters import (
    FilterSettings,
    MissingFieldPolicy,
)
from candidate_pipeline import (
    CandidatePipelineResult,
    run_candidate_pipeline,
)
from schwab_movers_source import (
    FREQUENCY_CHOICES,
    MARKET_CHOICES,
    SORT_CHOICES,
    fetch_schwab_movers,
)

DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"


def resolve_ecfg_path(explicit_path: Path | None) -> Path:
    """
    Resolve the Schwab encrypted-configuration path.

    Precedence:
        1. --ecfg
        2. MB_SCHWAB_ECFG
        3. MB_VAULT\\secure_schwabdev.ecfg
        4. .\\secure_schwabdev.ecfg
    """

    if explicit_path is not None:
        return explicit_path.expanduser()

    schwab_ecfg = os.environ.get("MB_SCHWAB_ECFG")
    if schwab_ecfg:
        return Path(schwab_ecfg).expanduser()

    vault = os.environ.get("MB_VAULT")
    if vault:
        return Path(vault).expanduser() / DEFAULT_ECFG_NAME

    return Path(DEFAULT_ECFG_NAME)


def first_value(
    mapping: Mapping[str, Any],
    names: Sequence[str],
) -> Any:
    """Return the first present, non-None value from a mapping."""

    for name in names:
        value = mapping.get(name)

        if value is not None:
            return value

    return None


def display_value(value: Any) -> str:
    """Convert an API value to compact display text."""

    if value is None:
        return "-"

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, int):
        return f"{value:,}"

    if isinstance(value, float):
        return f"{value:,.4f}"

    return str(value)


def display_percent_ratio(value: Any) -> str:
    """Display a decimal change ratio as a percentage."""

    if value is None:
        return "-"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 100:,.2f}%"

    return str(value)


def truncate_text(value: Any, width: int) -> str:
    """Convert and truncate text for a fixed-width table column."""

    text = display_value(value)

    if len(text) <= width:
        return text

    if width <= 3:
        return text[:width]

    return text[: width - 3] + "..."


def print_top_level_summary(data: Any) -> None:
    """Show the basic shape of the returned JSON document."""

    print()
    print("Response structure")
    print("=" * 72)
    print(f"Top-level type : {type(data).__name__}")

    if isinstance(data, Mapping):
        keys = [str(key) for key in data.keys()]
        print(f"Top-level keys : {', '.join(keys) if keys else '(none)'}")

    elif isinstance(data, list):
        print(f"Top-level items: {len(data)}")


def print_mover_summary(
    records: Sequence[Mapping[str, Any]],
) -> None:
    """Print a compact, schema-tolerant movers table."""

    print()
    print("Mover summary")
    print("=" * 104)
    print(
        f"{'#':>3}  "
        f"{'Symbol':<12}"
        f"{'Last':>14}"
        f"{'% Change':>14}"
        f"{'Volume':>18}  "
        f"{'Description':<39}"
    )
    print("-" * 104)

    if not records:
        print("No symbol records were found in the Schwab response.")
        print("=" * 104)
        return

    for index, record in enumerate(records, start=1):
        symbol = str(record.get("symbol", "")).strip().upper()

        last_price = first_value(
            record,
            (
                "lastPrice",
                "last",
                "mark",
                "closePrice",
                "regularMarketLastPrice",
            ),
        )

        percent_change = first_value(
            record,
            (
                "netPercentChange",
                "percentChange",
                "changePercent",
                "regularMarketPercentChange",
            ),
        )

        volume = first_value(
            record,
            (
                "volume",
                "totalVolume",
                "regularMarketVolume",
            ),
        )

        description = first_value(
            record,
            (
                "description",
                "companyName",
                "securityName",
                "name",
            ),
        )

        print(
            f"{index:>3}  "
            f"{truncate_text(symbol, 12):<12}"
            f"{truncate_text(last_price, 14):>14}"
            f"{display_percent_ratio(percent_change):>14}"
            f"{truncate_text(volume, 18):>18}  "
            f"{truncate_text(description, 39):<39}"
        )

    print("=" * 104)


def print_rejection_summary(
    result: CandidatePipelineResult,
) -> None:
    """Print rejected candidates and their filtering reasons."""

    print()
    print("Filter decisions")
    print("=" * 104)

    if not result.rejected:
        print("No candidates were rejected.")
        print("=" * 104)
        return

    for decision in result.rejected:
        reasons = "; ".join(decision.reasons)

        print(
            f"{decision.candidate.symbol:<12}"
            f"{reasons}"
        )

    print("=" * 104)


def save_outputs(
    *,
    output_dir: Path,
    data: Any,
    market: str,
    sort: str,
    frequency: int,
    records: Sequence[Mapping[str, Any]],
    pipeline_result: CandidatePipelineResult,
) -> tuple[Path, Path, Path]:
    """Save raw JSON, extracted symbols, and run metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")

    safe_market = market.replace("$", "").lower()
    safe_sort = sort.lower()

    stem = f"{timestamp}-movers-{safe_market}-{safe_sort}"

    raw_path = output_dir / f"{stem}-raw.json"
    symbols_path = output_dir / f"{stem}-symbols.txt"
    run_path = output_dir / f"{stem}-run.json"

    with raw_path.open("w", encoding="utf-8") as output_file:
        json.dump(
            data,
            output_file,
            indent=2,
            sort_keys=True,
        )
        output_file.write("\n")

    symbols = [
        str(record["symbol"]).strip().upper()
        for record in records
        if isinstance(record.get("symbol"), str)
    ]

    with symbols_path.open("w", encoding="utf-8") as output_file:
        for symbol in symbols:
            output_file.write(f"{symbol}\n")

    run_record = {
        "created_at": now.isoformat(timespec="seconds"),
        "source": "schwab_movers",
        "market": market,
        "sort": sort,
        "frequency": frequency,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "submitted": False,
        "watchlist_action": None,
        "raw_response_file": str(raw_path),
        "symbols_file": str(symbols_path),
        "pipeline_source": pipeline_result.source_name,
        "pipeline_evaluated_at": (
            pipeline_result.evaluated_at.isoformat(
                timespec="seconds"
            )
        ),

        "api_record_count": pipeline_result.input_count,
        "accepted_count": pipeline_result.accepted_count,
        "rejected_count": pipeline_result.rejected_count,
        "filters": {
            "min_price": pipeline_result.settings.min_price,
            "max_price": pipeline_result.settings.max_price,
            "min_volume": pipeline_result.settings.min_volume,
            "min_percent_change": (
                pipeline_result.settings.min_percent_change
            ),
            "max_percent_change": (
                pipeline_result.settings.max_percent_change
            ),
            "max_results": pipeline_result.settings.max_results,
            "missing_field_policy": (
                pipeline_result.settings
                .missing_field_policy
                .value
            ),

        },
        "rejections": [
            {
                "symbol": decision.candidate.symbol,
                "reasons": list(decision.reasons),
            }
            for decision in pipeline_result.rejected
        ],
    }

    with run_path.open("w", encoding="utf-8") as output_file:
        json.dump(
            run_record,
            output_file,
            indent=2,
        )
        output_file.write("\n")

    return raw_path, symbols_path, run_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve Schwab market movers and save the returned data. "
            "This version does not modify ThinkOrSwim."
        )
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
        help=(
            "Schwab movers frequency selector. "
            "Allowed values: 0, 1, 5, 10, 30, or 60. Default: 5."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of accepted candidates after filtering. "
            "Default: no additional limit."
        ),
    )

    parser.add_argument(
        "--min-price",
        type=float,
        default=None,
        help="Minimum last price. Default: no minimum.",
    )

    parser.add_argument(
        "--max-price",
        type=float,
        default=None,
        help="Maximum last price. Default: no maximum.",
    )

    parser.add_argument(
        "--min-volume",
        type=int,
        default=None,
        help="Minimum mover volume. Default: no minimum.",
    )

    parser.add_argument(
        "--min-percent-change",
        type=float,
        default=None,
        help=(
            "Minimum percentage change in percentage points. "
            "For example, 5 means 5%%."
        ),
    )

    parser.add_argument(
        "--max-percent-change",
        type=float,
        default=None,
        help=(
            "Maximum percentage change in percentage points. "
            "For example, 20 means 20%%."
        ),
    )

    parser.add_argument(
        "--missing-field-policy",
        choices=tuple(policy.value for policy in MissingFieldPolicy),
        default=MissingFieldPolicy.REJECT.value,
        help=(
            "Behavior when an enabled filter needs a missing value. "
            "Default: reject."
        ),
    )

    parser.add_argument(
        "--ecfg",
        type=Path,
        default=None,
        help=(
            "Path to secure_schwabdev.ecfg. Defaults to MB_SCHWAB_ECFG, "
            "then MB_VAULT\\secure_schwabdev.ecfg, then the current directory."
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
        help="Directory for generated output. Default: project output folder.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    if args.timeout < 1:
        print("ERROR: --timeout must be at least 1.", file=sys.stderr)
        return 2

    ecfg_path = resolve_ecfg_path(args.ecfg).resolve()

    print("Schwab movers probe")
    print("=" * 72)
    print(f"Encrypted config : {ecfg_path}")
    print(f"Market           : {args.market}")
    print(f"Sort             : {args.sort}")
    print(f"Frequency        : {args.frequency}")
    print(
        f"Limit            : "
        f"{args.limit if args.limit is not None else 'all returned symbols'}"
    )
    print(f"Timeout          : {args.timeout} seconds")
    print("Watchlist action : NONE")
    print()
    print("Filters")
    print("-" * 72)
    print(
        f"Minimum price    : "
        f"{filter_settings.min_price if filter_settings.min_price is not None else 'none'}"
    )
    print(
        f"Maximum price    : "
        f"{filter_settings.max_price if filter_settings.max_price is not None else 'none'}"
    )
    print(
        f"Minimum volume   : "
        f"{filter_settings.min_volume if filter_settings.min_volume is not None else 'none'}"
    )
    print(
        f"Minimum change   : "
        f"{filter_settings.min_percent_change if filter_settings.min_percent_change is not None else 'none'}"
    )
    print(
        f"Maximum change   : "
        f"{filter_settings.max_percent_change if filter_settings.max_percent_change is not None else 'none'}"
    )
    print(
        f"Missing fields   : "
        f"{filter_settings.missing_field_policy.value}"
    )


    if not ecfg_path.is_file():
        print(
            f"ERROR: Schwab encrypted configuration does not exist: {ecfg_path}",
            file=sys.stderr,
        )
        return 2

    password = getpass.getpass("ecfg password: ")

    client = None

    try:
        print()
        print("Creating Schwab client...")

        client = make_secure_schwab_client(
            ecfg_path,
            password,
            timeout=args.timeout,
        )

        print(f"Client created   : {type(client).__name__}")
        print("Requesting movers...")

# --------------------------------------------------------

        batch = fetch_schwab_movers(
            client,
            market=args.market,
            sort_name=args.sort,
            frequency=args.frequency,
        )

        print(f"Request URL      : {batch.request_url}")
        print(f"HTTP status      : {batch.status_code}")

        data = batch.raw_data

        print_top_level_summary(data)

        # filter_result = filter_candidates(
        #     batch.candidates,
        #     filter_settings,
        # )

        # accepted_symbols = {
        #     candidate.symbol
        #     for candidate in filter_result.accepted
        # }

        pipeline_result = run_candidate_pipeline(
            batch.candidates,
            filter_settings,
            source_name="schwab_movers",
            evaluated_at=batch.requested_at,
        )

        accepted_symbols = set(
            pipeline_result.accepted_symbols
        )

        records = [
            record
            for record in batch.records
            if str(record.get("symbol", "")).strip().upper()
            in accepted_symbols
        ]

        # print(f"API records      : {len(batch.records)}")
        # print(f"Accepted records : {len(filter_result.accepted)}")
        # print(f"Rejected records : {len(filter_result.rejected)}")
        # print(f"Local ordering   : {args.sort}")

        print(f"API records      : {pipeline_result.input_count}")
        print(f"Accepted records : {pipeline_result.accepted_count}")
        print(f"Rejected records : {pipeline_result.rejected_count}")
        print(f"Local ordering   : {args.sort}")


        print_mover_summary(records)
        # print_rejection_summary(filter_result)
        print_rejection_summary(pipeline_result)

        output_dir = args.output_dir.expanduser().resolve()

        # raw_path, symbols_path, run_path = save_outputs(
        #     output_dir=output_dir,
        #     data=data,
        #     market=args.market,
        #     sort=args.sort,
        #     frequency=args.frequency,
        #     records=records,
        #     filter_settings=filter_settings,
        #     filter_result=filter_result,
        # )

        raw_path, symbols_path, run_path = save_outputs(
            output_dir=output_dir,
            data=data,
            market=args.market,
            sort=args.sort,
            frequency=args.frequency,
            records=records,
            pipeline_result=pipeline_result,
        )

        print()
        print(f"Raw response     : {raw_path}")
        print(f"Extracted symbols: {symbols_path}")
        print(f"Run record       : {run_path}")
        print()
        print("No Watchlist command was published.")
        print("Schwab movers probe completed successfully.")
        return 0

    except SchwabdevNotInstalledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    except SecureSchwabConfigError as exc:
        print(f"ERROR: Invalid Schwab configuration: {exc}", file=sys.stderr)
        return 4

    except ValueError as exc:
        print(
            f"ERROR: Invalid Schwab Movers data or settings: {exc}",
            file=sys.stderr,
        )
        return 1

    except Exception as exc:
        print(
            f"ERROR: Schwab movers probe failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
