"""
Safely submit a symbol list to the ThinkOrSwim Default Watchlist.

Dry-run is the default. Use --submit to publish a command.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from watchlist_submission import (
    COMMAND_FOR_MODE,
    build_watchlist_command,
    normalize_symbols,
    submit_watchlist_symbols,
)


def read_symbol_file(path: Path) -> list[str]:
    """Read symbols from a UTF-8 text file."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Could not read symbol file {path}: {exc}"
        ) from exc

    return normalize_symbols([text])


def collect_symbols(
    command_line_symbols: Sequence[str] | None,
    symbol_file: Path | None,
) -> list[str]:
    """Combine command-line and file symbols."""

    raw_values: list[str] = []

    if command_line_symbols:
        raw_values.extend(command_line_symbols)

    if symbol_file is not None:
        raw_values.extend(
            read_symbol_file(symbol_file)
        )

    return normalize_symbols(raw_values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or submit symbols to the ThinkOrSwim "
            "Default Watchlist through mb-scan-command."
        )
    )

    parser.add_argument(
        "--mode",
        choices=sorted(COMMAND_FOR_MODE),
        required=True,
        help=(
            "'add' appends symbols; "
            "'replace' replaces the current Watchlist."
        ),
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        metavar="SYMBOL",
        help="Symbols separated by spaces or commas.",
    )

    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="UTF-8 text file containing symbols.",
    )

    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "Actually publish the command. "
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
        help="Directory for JSON run records.",
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

    if args.file is not None:
        args.file = args.file.expanduser().resolve()

        if not args.file.is_file():
            print(
                f"ERROR: Symbol file does not exist: "
                f"{args.file}",
                file=sys.stderr,
            )
            return 2

    try:
        symbols = collect_symbols(
            args.symbols,
            args.file,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not symbols:
        print(
            "ERROR: Supply symbols using "
            "--symbols, --file, or both.",
            file=sys.stderr,
        )
        return 2

    try:
        preview_command = build_watchlist_command(
            mode=args.mode,
            symbols=symbols,
            wait=args.wait,
            root=args.root,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Watchlist submission")
    print("=" * 60)
    print(f"Mode             : {args.mode}")
    print(
        f"Scanner command  : "
        f"{COMMAND_FOR_MODE[args.mode]}"
    )
    print(f"Symbol count     : {len(symbols)}")
    print(
        f"Submission       : "
        f"{'LIVE' if args.submit else 'DRY RUN'}"
    )
    print(
        "Command root     : "
        f"{args.root if args.root is not None else os.environ.get('MB_SCAN_CONTROL', '(not set)')}"
    )

    print()
    print("Symbols:")
    print(" ".join(symbols))

    print()
    print("Command:")
    print(
        subprocess.list2cmdline(
            list(preview_command)
        )
    )

    if args.submit:
        print()
        print("Publishing command...")

    try:
        result = submit_watchlist_symbols(
            mode=args.mode,
            symbols=symbols,
            submit=args.submit,
            wait=args.wait,
            root=args.root,
            output_dir=args.output_dir,
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
            f"Run record       : "
            f"{result.run_record_path}"
        )
        return 0

    print(
        f"mb-scan-command exit code: "
        f"{result.return_code}"
    )
    print(
        f"Run record               : "
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
        "Watchlist command was reported "
        "as processed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
