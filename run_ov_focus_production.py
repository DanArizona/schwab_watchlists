"""Build Focus r1 from production-eligible API-only OV evidence."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from schwab_watchlists.api_ov_focus_production import (
    load_api_ov_evidence,
    select_api_ov_symbols,
    write_api_ov_focus_artifacts,
)


ET = ZoneInfo("America/New_York")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify API-only OV evidence, select the top-N opening Focus "
            "BASE_SET, and write a strict schema-v2 r1 proposal."
        )
    )
    parser.add_argument(
        "--api-ov-manifest",
        required=True,
        type=Path,
        help="Production-eligible mb_market_data API OV manifest.",
    )
    parser.add_argument(
        "--opening-proposal",
        required=True,
        type=Path,
        help="Session opening_hierarchy_r0.json proposal.",
    )
    parser.add_argument(
        "--limit",
        required=True,
        type=int,
        help="Maximum number of ranked symbols in the Focus BASE_SET.",
    )
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


def build_decision_id(observed_at: datetime) -> str:
    return (
        "api-ov-focus-"
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
        print("API OV Focus production ERROR: --limit must be positive")
        return 2

    source_manifest = args.api_ov_manifest.expanduser().resolve()
    opening_path = args.opening_proposal.expanduser().resolve()
    generated_at = datetime.now(ET)
    output_dir = (
        args.output_dir.expanduser()
        if args.output_dir is not None
        else default_output_dir(args.output_root.expanduser(), generated_at)
    )

    try:
        if output_dir.exists():
            raise FileExistsError(
                f"output directory already exists: {output_dir}"
            )
        evidence, opening = load_api_ov_evidence(
            source_manifest,
            opening_proposal_path=opening_path,
        )
        if generated_at.date() != opening.session_date:
            raise ValueError(
                f"opening session {opening.session_date} is not today's "
                f"Eastern date {generated_at.date()}"
            )
        if generated_at >= opening.effective_at:
            raise ValueError(
                "API OV Focus production must complete before opening"
            )
        selection = select_api_ov_symbols(evidence, limit=args.limit)
        artifacts = write_api_ov_focus_artifacts(
            output_dir,
            opening_proposal_path=opening_path,
            evidence=evidence,
            opening=opening,
            selection=selection,
            requested_limit=args.limit,
            generated_at=generated_at,
            decision_id=build_decision_id(generated_at),
        )
    except Exception as error:
        print(
            f"API OV Focus production ERROR: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1

    print("API OV Focus production: PASS")
    print("=" * 79)
    print(f"Session date     : {opening.session_date}")
    print(f"Opening Uni      : {len(opening.uni_symbols):,}")
    print(f"Requested limit  : {args.limit:,}")
    print(f"API OV manifest  : {source_manifest}")
    print(f"Source rows      : {len(evidence.observations):,}")
    print(f"Eligible symbols : {selection.eligible_count:,}")
    print(f"Focus symbols    : {len(selection.selected_symbols):,}")
    print(f"Ranked Focus     : {' '.join(selection.selected_symbols)}")
    print(f"Revision         : r{artifacts.revision.revision}")
    print(f"Effective at     : {artifacts.revision.effective_at.isoformat()}")
    print(f"Content SHA-256  : {artifacts.revision.content_sha256}")
    print(f"Decision ledger  : {artifacts.decision_ledger}")
    print(f"Focus symbols CSV: {artifacts.focus_symbols}")
    print(f"r1 proposal      : {artifacts.proposal}")
    print(f"Manifest         : {artifacts.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
