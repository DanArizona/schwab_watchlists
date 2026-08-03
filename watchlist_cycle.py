"""Orchestration for one manually initiated, dry-run Watchlist cycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from candidate_filters import FilterSettings
from candidate_outputs import CandidateOutputPaths, write_candidate_outputs
from candidate_pipeline import CandidatePipelineResult, run_candidate_pipeline
from cycle_outputs import WatchlistCycleRecord, write_watchlist_cycle_record
from schwab_movers_source import SchwabMoversBatch
from watchlist_submission import (
    COMMAND_FOR_MODE,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
    WatchlistSubmissionResult,
    normalize_cycle_id,
    submit_watchlist_symbols,
)

CYCLE_STATUS_PLAN_CREATED = "plan_created"
CYCLE_STATUS_NO_CANDIDATES = "no_candidates"
MOVERS_DEMO_STRATEGY = "movers_demo_v1"
VALID_INPUT_MODES = frozenset({"api", "replay"})


@dataclass(frozen=True, slots=True)
class WatchlistCycleResult:
    """Complete result of one dry-run Watchlist cycle."""

    cycle_id: str
    started_at: datetime
    completed_at: datetime
    status: str
    strategy_name: str
    input_mode: str
    watchlist_mode: str
    pipeline_result: CandidatePipelineResult
    candidate_outputs: CandidateOutputPaths
    watchlist_plan: WatchlistSubmissionResult | None
    cycle_record_path: Path

    @property
    def plan_created(self) -> bool:
        """Whether the cycle produced a frozen Watchlist plan."""

        return self.watchlist_plan is not None


def generate_cycle_id(
    *,
    started_at: datetime | None = None,
    unique_suffix: str | None = None,
) -> str:
    """Create a unique, filename-safe Watchlist cycle identifier."""

    cycle_time = started_at or datetime.now().astimezone()
    suffix = (unique_suffix or uuid.uuid4().hex[:8]).strip().lower()

    if not suffix or not suffix.isalnum():
        raise ValueError(
            "Watchlist cycle suffix must contain only letters and numbers."
        )

    cycle_id = (
        f"cycle-{cycle_time.strftime('%Y%m%d-%H%M%S')}-{suffix}"
    )

    normalized = normalize_cycle_id(cycle_id)
    assert normalized is not None
    return normalized


def run_schwab_movers_cycle(
    *,
    batch: SchwabMoversBatch,
    filter_settings: FilterSettings,
    mode: str,
    output_dir: Path,
    input_mode: str,
    replay_path: Path | None = None,
    root: Path | None = None,
    wait: float = 30.0,
    strategy_name: str = MOVERS_DEMO_STRATEGY,
    cycle_id: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> WatchlistCycleResult:
    """
    Run a complete dry-run cycle from a prepared Schwab Movers batch.

    This function writes candidate outputs, optionally creates a frozen
    Watchlist plan, and writes a cycle summary. It never publishes a live
    scanner command.
    """

    if mode not in COMMAND_FOR_MODE:
        raise ValueError(f"Unsupported Watchlist mode: {mode}")

    if input_mode not in VALID_INPUT_MODES:
        raise ValueError(f"Unsupported cycle input mode: {input_mode}")

    if wait < 0:
        raise ValueError("Watchlist cycle wait cannot be negative.")

    normalized_strategy = strategy_name.strip()

    if not normalized_strategy:
        raise ValueError("Watchlist cycle strategy_name cannot be empty.")

    cycle_started_at = started_at or datetime.now().astimezone()
    normalized_cycle_id = (
        normalize_cycle_id(cycle_id)
        if cycle_id is not None
        else generate_cycle_id(started_at=cycle_started_at)
    )
    assert normalized_cycle_id is not None

    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_replay_path = (
        replay_path.expanduser().resolve()
        if replay_path is not None
        else None
    )

    if input_mode == "replay" and resolved_replay_path is None:
        raise ValueError("Replay cycles require replay_path.")

    if input_mode == "api" and resolved_replay_path is not None:
        raise ValueError("API cycles cannot specify replay_path.")

    pipeline_result = run_candidate_pipeline(
        batch.candidates,
        filter_settings,
        source_name="schwab_movers",
        evaluated_at=batch.requested_at,
    )

    market_slug = batch.market.lower().replace("$", "")
    sort_slug = batch.sort_name.lower()
    replay_suffix = "-replay" if input_mode == "replay" else ""
    candidate_stem = (
        f"{normalized_cycle_id}-movers-{market_slug}-{sort_slug}"
        f"{replay_suffix}"
    )

    candidate_outputs = write_candidate_outputs(
        output_dir=resolved_output_dir,
        stem=candidate_stem,
        raw_data=batch.raw_data,
        pipeline_result=pipeline_result,
        generated_at=cycle_started_at,
        extra_run_fields={
            "cycle_id": normalized_cycle_id,
            "strategy_name": normalized_strategy,
            "market": batch.market,
            "sort": batch.sort_name,
            "frequency": batch.frequency,
            "input_mode": input_mode,
            "replay_file": (
                str(resolved_replay_path)
                if resolved_replay_path is not None
                else None
            ),
            "request_url": batch.request_url if input_mode == "api" else None,
            "http_status": batch.status_code if input_mode == "api" else None,
            "watchlist_action": COMMAND_FOR_MODE[mode],
            "watchlist_mode": mode,
            "watchlist_submit_requested": False,
        },
    )

    watchlist_plan: WatchlistSubmissionResult | None = None

    if pipeline_result.accepted_symbols:
        watchlist_plan = submit_watchlist_symbols(
            mode=mode,
            symbols=pipeline_result.accepted_symbols,
            submit=False,
            wait=wait,
            root=root,
            output_dir=resolved_output_dir,
            created_at=cycle_started_at,
            record_origin=RECORD_ORIGIN_WATCHLIST_CYCLE,
            cycle_id=normalized_cycle_id,
        )
        status = CYCLE_STATUS_PLAN_CREATED
    else:
        status = CYCLE_STATUS_NO_CANDIDATES

    cycle_completed_at = completed_at or datetime.now().astimezone()

    cycle_record_path = write_watchlist_cycle_record(
        output_dir=resolved_output_dir,
        record=WatchlistCycleRecord(
            cycle_id=normalized_cycle_id,
            started_at=cycle_started_at,
            completed_at=cycle_completed_at,
            status=status,
            candidate_source="schwab_movers",
            strategy_name=normalized_strategy,
            input_mode=input_mode,
            watchlist_mode=mode,
            input_count=pipeline_result.input_count,
            accepted_count=pipeline_result.accepted_count,
            rejected_count=pipeline_result.rejected_count,
            accepted_symbols=pipeline_result.accepted_symbols,
            candidate_raw_file=candidate_outputs.raw_json,
            candidate_symbols_file=candidate_outputs.symbols_text,
            candidate_run_file=candidate_outputs.run_json,
            watchlist_plan_file=(
                watchlist_plan.run_record_path
                if watchlist_plan is not None
                else None
            ),
            replay_file=resolved_replay_path,
        ),
    )

    return WatchlistCycleResult(
        cycle_id=normalized_cycle_id,
        started_at=cycle_started_at,
        completed_at=cycle_completed_at,
        status=status,
        strategy_name=normalized_strategy,
        input_mode=input_mode,
        watchlist_mode=mode,
        pipeline_result=pipeline_result,
        candidate_outputs=candidate_outputs,
        watchlist_plan=watchlist_plan,
        cycle_record_path=cycle_record_path,
    )
