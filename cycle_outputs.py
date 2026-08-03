"""JSON audit output for one manually initiated Watchlist cycle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from watchlist_submission import normalize_cycle_id


@dataclass(frozen=True, slots=True)
class WatchlistCycleRecord:
    """JSON-compatible summary inputs for one Watchlist cycle."""

    cycle_id: str
    started_at: datetime
    completed_at: datetime
    status: str
    candidate_source: str
    strategy_name: str
    input_mode: str
    watchlist_mode: str
    input_count: int
    accepted_count: int
    rejected_count: int
    accepted_symbols: tuple[str, ...]
    candidate_raw_file: Path
    candidate_symbols_file: Path
    candidate_run_file: Path
    watchlist_plan_file: Path | None
    replay_file: Path | None = None


def write_watchlist_cycle_record(
    *,
    output_dir: Path,
    record: WatchlistCycleRecord,
) -> Path:
    """Write the durable summary record for one Watchlist cycle."""

    cycle_id = normalize_cycle_id(record.cycle_id)

    if cycle_id is None:
        raise ValueError("Watchlist cycle_id is required.")

    if record.completed_at < record.started_at:
        raise ValueError(
            "Watchlist cycle completion cannot precede its start."
        )

    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    output_path = resolved_output_dir / f"{cycle_id}-cycle-run.json"

    payload = {
        "cycle_id": cycle_id,
        "started_at": record.started_at.isoformat(timespec="seconds"),
        "completed_at": record.completed_at.isoformat(timespec="seconds"),
        "status": record.status,
        "candidate_source": record.candidate_source,
        "strategy_name": record.strategy_name,
        "input_mode": record.input_mode,
        "watchlist_mode": record.watchlist_mode,
        "input_count": record.input_count,
        "accepted_count": record.accepted_count,
        "rejected_count": record.rejected_count,
        "accepted_symbols": list(record.accepted_symbols),
        "candidate_raw_file": str(record.candidate_raw_file),
        "candidate_symbols_file": str(record.candidate_symbols_file),
        "candidate_run_file": str(record.candidate_run_file),
        "watchlist_plan_file": (
            str(record.watchlist_plan_file)
            if record.watchlist_plan_file is not None
            else None
        ),
        "replay_file": (
            str(record.replay_file)
            if record.replay_file is not None
            else None
        ),
    }

    with output_path.open("w", encoding="utf-8", newline="\n") as output_file:
        json.dump(payload, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")

    return output_path
