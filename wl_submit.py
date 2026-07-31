"""
Safely submit a symbol list to the ThinkOrSwim Default Watchlist.

The script is a wrapper around mb-scan-command.

Dry-run is the default. Use --submit to publish a command.

Examples
--------
Preview an add operation:

    python wl_submit.py --mode add --symbols AMD NVDA PLTR

Submit an add operation:

    python wl_submit.py --mode add --symbols AMD NVDA PLTR --submit

Preview symbols from a file:

    python wl_submit.py --mode replace --file symbols.txt

Submit a replacement and wait for processing:

    python wl_submit.py --mode replace --file symbols.txt --submit --wait 30
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence


COMMAND_FOR_MODE = {
    "add": "add_wl_symbols",
    "replace": "replace_wl_symbols",
}


def normalize_symbols(values: Sequence[str]) -> list[str]:
    """
    Uppercase and deduplicate symbols while preserving first-seen order.

    Values may contain symbols separated by spaces, commas, or newlines.
    """

    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = value.replace(",", " ")

        for part in cleaned.split():
            symbol = part.strip().upper()

            if not symbol:
                continue

            if symbol not in seen:
                seen.add(symbol)
                normalized.append(symbol)

    return normalized


def read_symbol_file(path: Path) -> list[str]:
    """Read symbols from a UTF-8 text file."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not read symbol file {path}: {exc}") from exc

    return normalize_symbols([text])


def collect_symbols(
    command_line_symbols: Sequence[str] | None,
    symbol_file: Path | None,
) -> list[str]:
    """Combine command-line and file symbols, then normalize them."""

    raw_values: list[str] = []

    if command_line_symbols:
        raw_values.extend(command_line_symbols)

    if symbol_file is not None:
        raw_values.extend(read_symbol_file(symbol_file))

    return normalize_symbols(raw_values)


def save_run_record(
    *,
    output_dir: Path,
    mode: str,
    symbols: Sequence[str],
    submitted: bool,
    command: Sequence[str] | None,
    return_code: int | None,
) -> Path:
    """Save a JSON record describing the proposed or submitted operation."""

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")

    output_path = output_dir / f"{timestamp}-wl-{mode}-run.json"

    record = {
        "created_at": now.isoformat(timespec="seconds"),
        "mode": mode,
        "scanner_command": COMMAND_FOR_MODE[mode],
        "submitted": submitted,
        "symbol_count": len(symbols),
        "symbols": list(symbols),
        "command": list(command) if command is not None else None,
        "return_code": return_code,
    }

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(record, output_file, indent=2)
        output_file.write("\n")

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or submit symbols to the ThinkOrSwim Default Watchlist "
            "through mb-scan-command."
        )
    )

    parser.add_argument(
        "--mode",
        choices=sorted(COMMAND_FOR_MODE),
        required=True,
        help=(
            "'add' appends symbols to the current Watchlist; "
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
        help="UTF-8 text file containing symbols separated by whitespace or commas.",
    )

    parser.add_argument(
        "--submit",
        action="store_true",
        help="Actually publish the command. Without this option, only preview it.",
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Maximum processing wait after submission. Default: 30.",
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Scanner command root. By default mb-scan-command uses "
            "MB_SCAN_CONTROL."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Directory for JSON run records. Default: project output folder.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.wait < 0:
        print("ERROR: --wait cannot be negative.", file=sys.stderr)
        return 2

    if args.file is not None:
        args.file = args.file.expanduser().resolve()

        if not args.file.is_file():
            print(
                f"ERROR: Symbol file does not exist: {args.file}",
                file=sys.stderr,
            )
            return 2

    try:
        symbols = collect_symbols(args.symbols, args.file)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not symbols:
        print(
            "ERROR: Supply symbols using --symbols, --file, or both.",
            file=sys.stderr,
        )
        return 2

    scanner_command = COMMAND_FOR_MODE[args.mode]

    command = [
        "mb-scan-command",
        scanner_command,
        "--symbols",
        *symbols,
    ]

    if args.root is not None:
        command.extend(["--root", str(args.root.expanduser().resolve())])

    if args.wait > 0:
        command.extend(["--wait", str(args.wait)])

    print("Watchlist submission")
    print("=" * 60)
    print(f"Mode             : {args.mode}")
    print(f"Scanner command  : {scanner_command}")
    print(f"Symbol count     : {len(symbols)}")
    print(f"Submission       : {'LIVE' if args.submit else 'DRY RUN'}")
    print(
        "Command root     : "
        f"{args.root if args.root is not None else os.environ.get('MB_SCAN_CONTROL', '(not set)')}"
    )
    print()
    print("Symbols:")
    print(" ".join(symbols))
    print()
    print("Command:")
    print(subprocess.list2cmdline(command))

    if not args.submit:
        record_path = save_run_record(
            output_dir=args.output_dir.expanduser().resolve(),
            mode=args.mode,
            symbols=symbols,
            submitted=False,
            command=command,
            return_code=None,
        )

        print()
        print("No command was published.")
        print(f"Run record       : {record_path}")
        return 0

    executable = shutil.which("mb-scan-command")

    if executable is None:
        print(
            "ERROR: mb-scan-command was not found on PATH.",
            file=sys.stderr,
        )
        return 3

    command[0] = executable

    print()
    print("Publishing command...")

    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
        )
    except OSError as exc:
        print(
            f"ERROR: Could not run mb-scan-command: {exc}",
            file=sys.stderr,
        )
        return 3

    record_path = save_run_record(
        output_dir=args.output_dir.expanduser().resolve(),
        mode=args.mode,
        symbols=symbols,
        submitted=True,
        command=command,
        return_code=completed.returncode,
    )

    print()
    print(f"mb-scan-command exit code: {completed.returncode}")
    print(f"Run record               : {record_path}")

    if completed.returncode != 0:
        print(
            "Watchlist command was not reported as successfully processed.",
            file=sys.stderr,
        )
        return completed.returncode

    print("Watchlist command was reported as processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
