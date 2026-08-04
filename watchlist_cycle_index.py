"""Discover Watchlist cycles and derive their current operational state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

CYCLE_STATE_PLAN_CREATED = "PLAN_CREATED"
CYCLE_STATE_PREVIEWED = "PREVIEWED"
CYCLE_STATE_APPLIED = "APPLIED"
CYCLE_STATE_NO_CHANGE = "NO_CHANGE"
CYCLE_STATE_APPLICATION_FAILED = "APPLICATION_FAILED"
CYCLE_STATE_NO_CANDIDATES = "NO_CANDIDATES"
CYCLE_STATE_PLAN_MISSING = "PLAN_MISSING"
CYCLE_STATE_NO_PLAN = "NO_PLAN"

VALID_CYCLE_STATES = frozenset({
    CYCLE_STATE_PLAN_CREATED,
    CYCLE_STATE_PREVIEWED,
    CYCLE_STATE_APPLIED,
    CYCLE_STATE_NO_CHANGE,
    CYCLE_STATE_APPLICATION_FAILED,
    CYCLE_STATE_NO_CANDIDATES,
    CYCLE_STATE_PLAN_MISSING,
    CYCLE_STATE_NO_PLAN,
})

PENDING_CYCLE_STATES = frozenset({
    CYCLE_STATE_PLAN_CREATED,
    CYCLE_STATE_PREVIEWED,
    CYCLE_STATE_APPLICATION_FAILED,
})


@dataclass(frozen=True, slots=True)
class WatchlistCycleEntry:
    """One cycle summary combined with its plan activity."""

    cycle_id: str
    started_at: str
    completed_at: str
    generation_status: str
    state: str
    candidate_source: str
    strategy_name: str
    input_mode: str
    watchlist_mode: str
    accepted_symbols: tuple[str, ...]
    cycle_record_path: Path
    plan_path: Path | None
    preview_run_path: Path | None = None
    previewed_at: str | None = None
    application_run_path: Path | None = None
    applied_at: str | None = None
    application_return_code: int | None = None
    no_change_against_cycle_id: str | None = None
    no_change_plan_path: Path | None = None
    no_change_application_path: Path | None = None
    no_change_applied_at: str | None = None

    @property
    def applied(self) -> bool:
        """Whether a linked successful live application exists."""

        return self.state == CYCLE_STATE_APPLIED

    @property
    def pending(self) -> bool:
        """Whether the cycle has an unapplied plan requiring attention."""

        return self.state in PENDING_CYCLE_STATES


@dataclass(frozen=True, slots=True)
class WatchlistCycleIndex:
    """Result of scanning an output directory for Watchlist cycles."""

    output_dir: Path
    cycles: tuple[WatchlistCycleEntry, ...]
    skipped_files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _DerivedRecord:
    """Internal preview or application record."""

    run_path: Path
    created_at: str
    return_code: int | None = None


def _canonical_path(path: Path) -> str:
    """Return a case-normalized absolute path for comparisons."""

    return os.path.normcase(
        str(path.expanduser().resolve(strict=False))
    )


def _parse_time(value: Any) -> datetime | None:
    """Parse one ISO timestamp."""

    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _time_key(value: str) -> float:
    parsed = _parse_time(value)

    if parsed is None:
        return float("-inf")

    return parsed.timestamp()


def _load_json_object(path: Path) -> dict[str, Any] | None:
    """Load a JSON object, returning None for unreadable or invalid data."""

    try:
        with path.open("r", encoding="utf-8") as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _resolve_record_path(
    value: str,
    *,
    containing_file: Path,
) -> Path:
    """Resolve an absolute or record-relative path."""

    path = Path(value).expanduser()

    if not path.is_absolute():
        path = containing_file.parent / path

    return path.resolve(strict=False)


def _store_latest(
    records: dict[str, _DerivedRecord],
    key: str,
    candidate: _DerivedRecord,
) -> None:
    current = records.get(key)

    if (
        current is None
        or _time_key(candidate.created_at)
        > _time_key(current.created_at)
    ):
        records[key] = candidate


def discover_watchlist_cycles(
    output_dir: Path,
) -> WatchlistCycleIndex:
    """
    Discover cycle summaries and derive lifecycle state from linked records.

    The original cycle record is never rewritten. Preview and application
    records are joined through source_plan_file, allowing the current state
    to be reconstructed without modifying historical audit files.
    """

    resolved_output_dir = (
        output_dir.expanduser().resolve(strict=False)
    )

    if not resolved_output_dir.is_dir():
        raise ValueError(
            "Watchlist output directory does not exist: "
            f"{resolved_output_dir}"
        )

    skipped_files: list[Path] = []
    previews: dict[str, _DerivedRecord] = {}
    applications: dict[str, _DerivedRecord] = {}
    successful_applications: dict[str, _DerivedRecord] = {}

    for run_path in sorted(
        resolved_output_dir.glob("*-wl-*-run.json")
    ):
        data = _load_json_object(run_path)

        if data is None:
            skipped_files.append(run_path)
            continue

        source_plan_file = data.get("source_plan_file")
        created_at = data.get("created_at")

        if (
            not isinstance(source_plan_file, str)
            or not source_plan_file.strip()
            or _parse_time(created_at) is None
        ):
            continue

        source_path = _resolve_record_path(
            source_plan_file,
            containing_file=run_path,
        )
        source_key = _canonical_path(source_path)
        record_origin = data.get("record_origin")
        submitted = data.get("submitted")
        resolved_run_path = run_path.resolve(strict=False)

        if (
            record_origin == "plan_preview"
            and submitted is False
        ):
            _store_latest(
                previews,
                source_key,
                _DerivedRecord(
                    run_path=resolved_run_path,
                    created_at=created_at,
                ),
            )
            continue

        if submitted is not True:
            continue

        return_code = data.get("return_code")

        if return_code is not None and not isinstance(
            return_code,
            int,
        ):
            continue

        application = _DerivedRecord(
            run_path=resolved_run_path,
            created_at=created_at,
            return_code=return_code,
        )
        _store_latest(
            applications,
            source_key,
            application,
        )

        if return_code == 0:
            _store_latest(
                successful_applications,
                source_key,
                application,
            )

    cycles: list[WatchlistCycleEntry] = []

    for cycle_path in sorted(
        resolved_output_dir.glob("cycle-*-cycle-run.json")
    ):
        data = _load_json_object(cycle_path)

        if data is None:
            skipped_files.append(cycle_path)
            continue

        cycle_id = data.get("cycle_id")
        started_at = data.get("started_at")
        completed_at = data.get("completed_at")
        generation_status = data.get("status")
        candidate_source = data.get("candidate_source")
        strategy_name = data.get("strategy_name")
        input_mode = data.get("input_mode")
        watchlist_mode = data.get("watchlist_mode")
        raw_symbols = data.get("accepted_symbols")
        raw_plan_path = data.get("watchlist_plan_file")
        raw_no_change_cycle_id = data.get(
            "no_change_against_cycle_id"
        )
        raw_no_change_plan_path = data.get(
            "no_change_plan_file"
        )
        raw_no_change_application_path = data.get(
            "no_change_application_file"
        )
        raw_no_change_applied_at = data.get(
            "no_change_applied_at"
        )

        valid = (
            isinstance(cycle_id, str)
            and bool(cycle_id.strip())
            and _parse_time(started_at) is not None
            and _parse_time(completed_at) is not None
            and isinstance(generation_status, str)
            and isinstance(candidate_source, str)
            and isinstance(strategy_name, str)
            and isinstance(input_mode, str)
            and isinstance(watchlist_mode, str)
            and isinstance(raw_symbols, list)
            and all(
                isinstance(symbol, str)
                for symbol in raw_symbols
            )
            and (
                raw_plan_path is None
                or isinstance(raw_plan_path, str)
            )
            and (
                raw_no_change_cycle_id is None
                or isinstance(raw_no_change_cycle_id, str)
            )
            and (
                raw_no_change_plan_path is None
                or isinstance(raw_no_change_plan_path, str)
            )
            and (
                raw_no_change_application_path is None
                or isinstance(raw_no_change_application_path, str)
            )
            and (
                raw_no_change_applied_at is None
                or _parse_time(raw_no_change_applied_at) is not None
            )
        )

        if not valid:
            skipped_files.append(cycle_path)
            continue

        plan_path = (
            _resolve_record_path(
                raw_plan_path,
                containing_file=cycle_path,
            )
            if raw_plan_path is not None
            else None
        )
        no_change_plan_path = (
            _resolve_record_path(
                raw_no_change_plan_path,
                containing_file=cycle_path,
            )
            if raw_no_change_plan_path is not None
            else None
        )
        no_change_application_path = (
            _resolve_record_path(
                raw_no_change_application_path,
                containing_file=cycle_path,
            )
            if raw_no_change_application_path is not None
            else None
        )
        preview: _DerivedRecord | None = None
        application: _DerivedRecord | None = None
        successful_application: _DerivedRecord | None = None

        if generation_status == "no_change":
            state = CYCLE_STATE_NO_CHANGE
        elif plan_path is None:
            state = (
                CYCLE_STATE_NO_CANDIDATES
                if generation_status == "no_candidates"
                else CYCLE_STATE_NO_PLAN
            )
        else:
            plan_key = _canonical_path(plan_path)
            preview = previews.get(plan_key)
            application = applications.get(plan_key)
            successful_application = (
                successful_applications.get(plan_key)
            )

            if successful_application is not None:
                state = CYCLE_STATE_APPLIED
                application = successful_application
            elif application is not None:
                state = CYCLE_STATE_APPLICATION_FAILED
            elif preview is not None:
                state = CYCLE_STATE_PREVIEWED
            elif plan_path.is_file():
                state = CYCLE_STATE_PLAN_CREATED
            else:
                state = CYCLE_STATE_PLAN_MISSING

        cycles.append(
            WatchlistCycleEntry(
                cycle_id=cycle_id.strip(),
                started_at=started_at,
                completed_at=completed_at,
                generation_status=generation_status,
                state=state,
                candidate_source=candidate_source,
                strategy_name=strategy_name,
                input_mode=input_mode,
                watchlist_mode=watchlist_mode,
                accepted_symbols=tuple(raw_symbols),
                cycle_record_path=(
                    cycle_path.resolve(strict=False)
                ),
                plan_path=plan_path,
                preview_run_path=(
                    preview.run_path
                    if preview is not None
                    else None
                ),
                previewed_at=(
                    preview.created_at
                    if preview is not None
                    else None
                ),
                application_run_path=(
                    application.run_path
                    if application is not None
                    else None
                ),
                applied_at=(
                    application.created_at
                    if (
                        state == CYCLE_STATE_APPLIED
                        and application is not None
                    )
                    else None
                ),
                application_return_code=(
                    application.return_code
                    if application is not None
                    else None
                ),
                no_change_against_cycle_id=(
                    raw_no_change_cycle_id
                ),
                no_change_plan_path=no_change_plan_path,
                no_change_application_path=(
                    no_change_application_path
                ),
                no_change_applied_at=(
                    raw_no_change_applied_at
                ),
            )
        )

    cycles.sort(
        key=lambda cycle: _time_key(cycle.started_at),
        reverse=True,
    )

    return WatchlistCycleIndex(
        output_dir=resolved_output_dir,
        cycles=tuple(cycles),
        skipped_files=tuple(skipped_files),
    )
