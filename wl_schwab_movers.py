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


DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"

MARKET_CHOICES = (
    "$DJI",
    "$COMPX",
    "$SPX",
    "NYSE",
    "NASDAQ",
    "OTCBB",
    "INDEX_ALL",
    "EQUITY_ALL",
    "OPTION_ALL",
    "OPTION_PUT",
    "OPTION_CALL",
)

SORT_CHOICES = (
    "VOLUME",
    "TRADES",
    "PERCENT_CHANGE_UP",
    "PERCENT_CHANGE_DOWN",
)

FREQUENCY_CHOICES = (
    0,
    1,
    5,
    10,
    30,
    60,
)


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


def find_symbol_records(value: Any) -> list[Mapping[str, Any]]:
    """
    Recursively find dictionaries containing a nonempty 'symbol' field.

    The first API run is intentionally schema-tolerant so that the complete
    response can be examined before selection logic depends on exact fields.
    """

    records: list[Mapping[str, Any]] = []

    if isinstance(value, Mapping):
        symbol = value.get("symbol")

        if isinstance(symbol, str) and symbol.strip():
            records.append(value)

        for child in value.values():
            records.extend(find_symbol_records(child))

    elif isinstance(value, list):
        for child in value:
            records.extend(find_symbol_records(child))

    return records


def deduplicate_records(
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Deduplicate records by uppercase symbol while preserving order."""

    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        raw_symbol = record.get("symbol")

        if not isinstance(raw_symbol, str):
            continue

        symbol = raw_symbol.strip().upper()

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)
        result.append(record)

    return result


def sort_mover_records(
    records: Sequence[Mapping[str, Any]],
    sort_name: str,
) -> list[Mapping[str, Any]]:
    """Sort mover records deterministically using returned Schwab fields."""

    if sort_name == "VOLUME":
        return sorted(
            records,
            key=lambda record: record.get("volume") or 0,
            reverse=True,
        )

    if sort_name == "TRADES":
        return sorted(
            records,
            key=lambda record: record.get("trades") or 0,
            reverse=True,
        )

    if sort_name == "PERCENT_CHANGE_UP":
        return sorted(
            records,
            key=lambda record: record.get("netPercentChange") or 0,
            reverse=True,
        )

    if sort_name == "PERCENT_CHANGE_DOWN":
        return sorted(
            records,
            key=lambda record: record.get("netPercentChange") or 0,
        )

    raise ValueError(f"Unsupported mover sort: {sort_name}")


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


def save_outputs(
    *,
    output_dir: Path,
    data: Any,
    market: str,
    sort: str,
    frequency: int,
    records: Sequence[Mapping[str, Any]],
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
            "Maximum number of extracted symbols to display and save. "
            "Default: retain all returned symbols."
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

    if args.limit is not None and args.limit < 1:
        print("ERROR: --limit must be at least 1.", file=sys.stderr)
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

        response = client.movers(
            symbol=args.market,
            sort=args.sort,
            frequency=args.frequency,
        )

        print(f"Request URL      : {response.url}")
        print(f"HTTP status      : {response.status_code}")
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            print(
                f"ERROR: Schwab response was not valid JSON: {exc}",
                file=sys.stderr,
            )
            return 1

        print_top_level_summary(data)

        discovered_records = find_symbol_records(data)
        api_records = deduplicate_records(discovered_records)

        records = sort_mover_records(
            api_records,
            args.sort,
        )

        if args.limit is not None:
            records = records[: args.limit]

        print(f"API records      : {len(api_records)}")
        print(f"Selected records : {len(records)}")
        print(f"Local ordering   : {args.sort}")

        print_mover_summary(records)

        output_dir = args.output_dir.expanduser().resolve()

        raw_path, symbols_path, run_path = save_outputs(
            output_dir=output_dir,
            data=data,
            market=args.market,
            sort=args.sort,
            frequency=args.frequency,
            records=records,
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
