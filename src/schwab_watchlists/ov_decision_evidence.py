from __future__ import annotations

import hashlib
import json

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from mb_market_data.decision_batch import (
    DecisionSnapshotBatch,
)

from schwab_watchlists.ov_coordinator import (
    OVSelection,
)


OV_DECISION_EVIDENCE_SCHEMA_VERSION = 1


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Enum):
        return value.value

    raise TypeError(
        f"Object of type {type(value).__name__} "
        "is not JSON serializable."
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for chunk in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def write_ov_decision_evidence(
    path: Path,
    *,
    intent_id: str,
    batch: DecisionSnapshotBatch,
    selection: OVSelection,
    source_watchlist_path: Path,
    requested_limit: int,
) -> Path:
    """
    Persist one OV BASE_SET decision as JSON Lines.

    The first record describes the complete decision batch.
    Each following record contains one DecisionSnapshot and
    its corresponding OVDecisionEvaluation.

    The source ThinkOrSwim CSV is identified by path and
    SHA-256 digest so the normalized evidence can always be
    tied back to the exact raw input file.
    """
    path = Path(path).expanduser()
    source_watchlist_path = (
        Path(source_watchlist_path).expanduser()
    )

    intent_id = intent_id.strip()

    if not intent_id:
        raise ValueError(
            "intent_id must not be empty."
        )

    if requested_limit <= 0:
        raise ValueError(
            "requested_limit must be positive."
        )

    if not source_watchlist_path.is_file():
        raise FileNotFoundError(
            source_watchlist_path
        )

    evaluation_by_symbol = {
        evaluation.symbol: evaluation
        for evaluation in selection.evaluations
    }

    snapshot_symbols = {
        snapshot.symbol
        for snapshot in batch.snapshots
    }

    evaluation_symbols = set(
        evaluation_by_symbol
    )

    if snapshot_symbols != evaluation_symbols:
        raise ValueError(
            "OV selection evaluations must exactly "
            "match DecisionSnapshotBatch symbols."
        )

    header = {
        "record_type": "batch",
        "schema_version": (
            OV_DECISION_EVIDENCE_SCHEMA_VERSION
        ),
        "intent_id": intent_id,
        "trade_date": batch.trade_date,
        "tos_observed_at_utc": (
            batch.tos_observed_at_utc
        ),
        "source_watchlist_path": str(
            source_watchlist_path
        ),
        "source_watchlist_sha256": (
            _sha256_file(
                source_watchlist_path
            )
        ),
        "requested_limit": requested_limit,
        "selected_symbols": (
            selection.selected_symbols
        ),
        "eligible_count": (
            selection.eligible_count
        ),
        "excluded_symbols": (
            selection.excluded_symbols
        ),
        "quote_request_count": (
            batch.quote_request_count
        ),
        "quote_batch_size": (
            batch.quote_batch_size
        ),
        "unexpected_quote_symbols": (
            batch.unexpected_quote_symbols
        ),
        "quote_results_not_in_watchlist": (
            batch.quote_results_not_in_watchlist
        ),
    }

    records = [header]

    for snapshot in batch.snapshots:
        evaluation = evaluation_by_symbol[
            snapshot.symbol
        ]

        records.append(
            {
                "record_type": "symbol",
                "schema_version": (
                    OV_DECISION_EVIDENCE_SCHEMA_VERSION
                ),
                "symbol": snapshot.symbol,
                "snapshot": asdict(
                    snapshot
                ),
                "evaluation": asdict(
                    evaluation
                ),
            }
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.name + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output:
        for record in records:
            output.write(
                json.dumps(
                    record,
                    default=_json_default,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            output.write("\n")

    temp_path.replace(path)

    return path
