"""
Verify Schwab authentication and retrieve quotes without modifying ThinkOrSwim.

Examples
--------
Use the default symbols:

    python schwab_api_probe.py

Specify symbols:

    python schwab_api_probe.py --symbols AAPL AMD NVDA

Specify the encrypted configuration explicitly:

    python schwab_api_probe.py --ecfg "C:\\path\\to\\secure_schwabdev.ecfg"
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import SecureSchwabConfigError


DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"
DEFAULT_SYMBOLS = ("AAPL", "AMD", "MSFT")


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


def normalize_symbols(values: Sequence[str]) -> list[str]:
    """Uppercase and deduplicate symbols while preserving order."""

    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        for part in value.replace(",", " ").split():
            symbol = part.strip().upper()

            if symbol and symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)

    return normalized


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
    """Convert a quote value to compact display text."""

    if value is None:
        return "-"

    if isinstance(value, float):
        return f"{value:,.4f}"

    if isinstance(value, int):
        return f"{value:,}"

    return str(value)


def print_quote_summary(
    requested_symbols: Sequence[str],
    data: Mapping[str, Any],
) -> None:
    """Print a compact summary without depending on every field existing."""

    print()
    print("Quote summary")
    print("=" * 88)
    print(
        f"{'Symbol':<10}"
        f"{'Last':>14}"
        f"{'Bid':>14}"
        f"{'Ask':>14}"
        f"{'% Change':>14}"
        f"{'Volume':>18}"
    )
    print("-" * 88)

    for requested_symbol in requested_symbols:
        record = data.get(requested_symbol)

        if not isinstance(record, Mapping):
            print(f"{requested_symbol:<10}{'No quote record returned':>78}")
            continue

        quote = record.get("quote", {})
        regular = record.get("regular", {})

        if not isinstance(quote, Mapping):
            quote = {}

        if not isinstance(regular, Mapping):
            regular = {}

        last_price = first_value(
            quote,
            (
                "lastPrice",
                "mark",
                "closePrice",
            ),
        )

        if last_price is None:
            last_price = first_value(
                regular,
                (
                    "regularMarketLastPrice",
                    "regularMarketPrice",
                ),
            )

        bid_price = first_value(
            quote,
            (
                "bidPrice",
                "bid",
            ),
        )

        ask_price = first_value(
            quote,
            (
                "askPrice",
                "ask",
            ),
        )

        percent_change = first_value(
            quote,
            (
                "netPercentChange",
                "regularMarketPercentChange",
                "markPercentChange",
            ),
        )

        volume = first_value(
            quote,
            (
                "totalVolume",
                "regularMarketVolume",
            ),
        )

        print(
            f"{requested_symbol:<10}"
            f"{display_value(last_price):>14}"
            f"{display_value(bid_price):>14}"
            f"{display_value(ask_price):>14}"
            f"{display_value(percent_change):>14}"
            f"{display_value(volume):>18}"
        )

    print("=" * 88)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create an authenticated Schwab client and retrieve harmless "
            "market-data quotes."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help=(
            "Symbols to request. Spaces and commas are accepted. "
            "Default: AAPL AMD MSFT."
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
        help="Directory for the raw JSON response. Default: project output folder.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    symbols = normalize_symbols(args.symbols)

    if not symbols:
        print("ERROR: No symbols were supplied.", file=sys.stderr)
        return 2

    ecfg_path = resolve_ecfg_path(args.ecfg).resolve()

    print("Schwab API probe")
    print("=" * 60)
    print(f"Encrypted config : {ecfg_path}")
    print(f"Symbols          : {', '.join(symbols)}")
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
        print(f"Requesting quotes: {', '.join(symbols)}")

        response = client.quotes(
            symbols,
            fields="quote",
        )

        print(f"HTTP status      : {response.status_code}")
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, Mapping):
            print(
                "ERROR: Schwab returned JSON, but the top-level value "
                "was not a dictionary.",
                file=sys.stderr,
            )
            return 1

        output_dir = args.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        output_path = output_dir / f"{timestamp}-quotes-raw.json"

        with output_path.open("w", encoding="utf-8") as output_file:
            json.dump(
                data,
                output_file,
                indent=2,
                sort_keys=True,
            )
            output_file.write("\n")

        print_quote_summary(symbols, data)

        print()
        print(f"Raw response saved: {output_path}")
        print("Schwab API probe completed successfully.")
        return 0

    except SchwabdevNotInstalledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    except SecureSchwabConfigError as exc:
        print(f"ERROR: Invalid Schwab configuration: {exc}", file=sys.stderr)
        return 4

    except json.JSONDecodeError as exc:
        print(f"ERROR: Schwab response was not valid JSON: {exc}", file=sys.stderr)
        return 1

    except Exception as exc:
        print(
            f"ERROR: Schwab API probe failed: {type(exc).__name__}: {exc}",
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
