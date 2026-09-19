"""Build the daily OV-derived Focus BASE_SET and schema-v2 r1 proposal."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from mb_tools.schwab_secure.client import (
    SchwabdevNotInstalledError,
    make_secure_schwab_client,
)
from mb_tools.schwab_secure.config import SecureSchwabConfigError

from schwab_watchlists.ov_coordinator import (
    acquire_live_ov_batch,
    select_ov_symbols,
)
from schwab_watchlists.ov_focus_production import (
    load_hierarchy_proposal,
    validate_complete_uni_coverage,
    write_ov_focus_artifacts,
)


ET = ZoneInfo("America/New_York")
DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
DEFAULT_ECFG_NAME = "secure_schwabdev.ecfg"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire same-day OV_DECISION and Schwab evidence, select the "
            "top-N Focus BASE_SET within opening Uni, and write a strict "
            "schema-v2 r1 proposal."
        )
    )
    parser.add_argument(
        "--watchlist",
        required=True,
        type=Path,
        help="Same-day ToS Watchlist CSV containing OV_DECISION.",
    )
    parser.add_argument(
        "--opening-proposal",
        required=True,
        type=Path,
        help="Published session opening_hierarchy_r0.json proposal.",
    )
    parser.add_argument(
        "--limit",
        required=True,
        type=int,
        help="Maximum number of ranked symbols in the Focus BASE_SET.",
    )
    parser.add_argument(
        "--watchlist-date",
        type=parse_date,
        help=(
            "Explicit source date for an undated Watchlist filename. If the "
            "filename contains YYYY-MM-DD, both values must agree."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=400)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--ecfg", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output") / "ov_focus_production",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Explicit immutable output directory; overrides --output-root.",
    )
    return parser.parse_args()


def extract_filename_date(path: Path) -> date | None:
    """Return the first valid YYYY-MM-DD embedded in a filename."""

    match = DATE_PATTERN.search(path.name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def resolve_source_date(
    path: Path,
    explicit_date: date | None,
) -> date:
    """Resolve and cross-check the ToS export's session date."""

    filename_date = extract_filename_date(path)
    if explicit_date is not None:
        if filename_date is not None and filename_date != explicit_date:
            raise ValueError(
                f"--watchlist-date {explicit_date} differs from filename "
                f"date {filename_date}"
            )
        return explicit_date
    if filename_date is None:
        raise ValueError(
            "Watchlist filename contains no YYYY-MM-DD; supply "
            "--watchlist-date"
        )
    return filename_date


def resolve_ecfg_path(explicit_path: Path | None) -> Path:
    """Resolve the encrypted Schwab configuration using project precedence."""

    if explicit_path is not None:
        return explicit_path.expanduser()
    configured = os.environ.get("MB_SCHWAB_ECFG", "").strip()
    if configured:
        return Path(configured).expanduser()
    vault = os.environ.get("MB_VAULT", "").strip()
    if vault:
        return Path(vault).expanduser() / DEFAULT_ECFG_NAME
    return Path(DEFAULT_ECFG_NAME)


def build_decision_id(observed_at: datetime) -> str:
    return (
        "ov-focus-"
        + observed_at.astimezone(ET).strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:8]
    )


def default_output_dir(root: Path, observed_at: datetime) -> Path:
    timestamp = observed_at.astimezone(ET).strftime("%Y-%m-%d-%H-%M-%S")
    return root / timestamp


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        print("OV Focus production ERROR: --limit must be positive")
        return 2
    if args.batch_size <= 0:
        print("OV Focus production ERROR: --batch-size must be positive")
        return 2
    if args.timeout <= 0:
        print("OV Focus production ERROR: --timeout must be positive")
        return 2

    watchlist_path = args.watchlist.expanduser().resolve()
    opening_path = args.opening_proposal.expanduser().resolve()
    observed_at = datetime.now(ET)
    output_dir = (
        args.output_dir.expanduser()
        if args.output_dir is not None
        else default_output_dir(args.output_root.expanduser(), observed_at)
    )

    try:
        if not watchlist_path.is_file():
            raise FileNotFoundError(watchlist_path)
        if not opening_path.is_file():
            raise FileNotFoundError(opening_path)
        if output_dir.exists():
            raise FileExistsError(
                f"output directory already exists: {output_dir}"
            )
        opening = load_hierarchy_proposal(opening_path)
        if opening.revision != 0:
            raise ValueError("opening proposal must be revision r0")
        if opening.focus_symbols or opening.hot_symbols:
            raise ValueError("opening r0 must have empty Focus and Hot")
        source_date = resolve_source_date(
            watchlist_path,
            args.watchlist_date,
        )
        if source_date != opening.session_date:
            raise ValueError(
                f"Watchlist date {source_date} differs from opening "
                f"session {opening.session_date}"
            )
        if observed_at.date() != opening.session_date:
            raise ValueError(
                f"opening session {opening.session_date} is not today's "
                f"Eastern date {observed_at.date()}"
            )
        if observed_at >= opening.effective_at:
            raise ValueError(
                "OV Focus production must complete before the opening "
                "revision's effective time"
            )
    except (OSError, TypeError, ValueError) as error:
        print(
            f"OV Focus production ERROR: {type(error).__name__}: {error}"
        )
        return 1

    ecfg_path = resolve_ecfg_path(args.ecfg).resolve()
    if not ecfg_path.is_file():
        print(f"OV Focus production ERROR: missing config: {ecfg_path}")
        return 1

    print("OV Focus production")
    print("=" * 79)
    print(f"Session date     : {opening.session_date}")
    print(f"Opening Uni      : {len(opening.uni_symbols):,}")
    print(f"Requested limit  : {args.limit:,}")
    print(f"ToS Watchlist    : {watchlist_path}")
    print(f"Opening proposal : {opening_path}")
    print(f"Encrypted config : {ecfg_path}")
    print()

    client = None
    try:
        password = getpass.getpass("Encrypted config password: ")
        client = make_secure_schwab_client(
            ecfg_path,
            password,
            timeout=args.timeout,
        )
        batch = acquire_live_ov_batch(
            client,
            watchlist_path,
            trade_date=opening.session_date,
            fields="all",
            batch_size=args.batch_size,
        )
        extra_source_symbols = validate_complete_uni_coverage(batch, opening)
        selection = select_ov_symbols(
            batch,
            limit=args.limit,
            allowed_symbols=opening.uni_symbols,
        )
        generated_at = datetime.now(ET)
        if generated_at >= opening.effective_at:
            raise ValueError(
                "OV acquisition completed too late to publish opening r1"
            )
        artifacts = write_ov_focus_artifacts(
            output_dir,
            opening_proposal_path=opening_path,
            source_watchlist_path=watchlist_path,
            batch=batch,
            selection=selection,
            requested_limit=args.limit,
            generated_at=generated_at,
            decision_id=build_decision_id(generated_at),
        )
    except SchwabdevNotInstalledError as error:
        print(f"OV Focus production ERROR: {error}", file=sys.stderr)
        return 3
    except SecureSchwabConfigError as error:
        print(
            f"OV Focus production ERROR: invalid config: {error}",
            file=sys.stderr,
        )
        return 4
    except Exception as error:
        print(
            f"OV Focus production ERROR: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    print()
    print("OV Focus production: PASS")
    print("=" * 79)
    print(f"Source rows      : {len(batch.snapshots):,}")
    print(f"Extra source rows: {len(extra_source_symbols):,}")
    print(f"Eligible symbols : {selection.eligible_count:,}")
    print(f"Focus symbols    : {len(selection.selected_symbols):,}")
    print(f"Ranked Focus     : {' '.join(selection.selected_symbols)}")
    print(f"Revision         : r{artifacts.revision.revision}")
    print(f"Effective at     : {artifacts.revision.effective_at.isoformat()}")
    print(f"Content SHA-256  : {artifacts.revision.content_sha256}")
    print(f"Decision ledger  : {artifacts.decision_ledger}")
    print(f"OV evidence      : {artifacts.evidence}")
    print(f"Focus symbols CSV: {artifacts.focus_symbols}")
    print(f"r1 proposal      : {artifacts.proposal}")
    print(f"Manifest         : {artifacts.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
