from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import wl_check_cycle_due
from watchlist_cycle import generate_cycle_id
from watchlist_cycle_schedule import (
    SCHEDULE_STATUS_DUE,
    SCHEDULE_STATUS_NON_TRADING_DAY,
    SCHEDULE_STATUS_NOT_DUE,
    SCHEDULE_STATUS_NO_PRIOR_CYCLE,
    SCHEDULE_STATUS_OUTSIDE_SESSION,
    evaluate_output_watchlist_cycle_schedule,
    evaluate_watchlist_cycle_schedule,
    format_watchlist_cycle_schedule_decision,
)

EASTERN = ZoneInfo("America/New_York")
CENTRAL = ZoneInfo("America/Chicago")


def at(
    hour: int,
    minute: int,
    second: int = 0,
    *,
    day: int = 4,
) -> datetime:
    return datetime(
        2026,
        8,
        day,
        hour,
        minute,
        second,
        tzinfo=EASTERN,
    )


def write_cycle(
    output_dir: Path,
    *,
    cycle_id: str,
    started_at: datetime,
) -> Path:
    path = output_dir / f"{cycle_id}-cycle-run.json"
    path.write_text(
        json.dumps(
            {
                "cycle_id": cycle_id,
                "started_at": started_at.isoformat(
                    timespec="seconds"
                ),
                "completed_at": (
                    started_at + timedelta(seconds=2)
                ).isoformat(timespec="seconds"),
                "status": "no_candidates",
                "candidate_source": "schwab_movers",
                "strategy_name": "movers_demo_v1",
                "input_mode": "api",
                "watchlist_mode": "replace",
                "accepted_symbols": [],
                "watchlist_plan_file": None,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_after_close_is_outside_session() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(19, 42)
    )

    assert decision.status == (
        SCHEDULE_STATUS_OUTSIDE_SESSION
    )
    assert not decision.due
    assert decision.phase == "closed"


def test_weekend_is_non_trading_day() -> None:
    # August 8, 2026 is a Saturday.
    decision = evaluate_watchlist_cycle_schedule(
        now=at(10, 0, day=8)
    )

    assert decision.status == (
        SCHEDULE_STATUS_NON_TRADING_DAY
    )
    assert not decision.due


def test_first_session_cycle_is_due() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 30)
    )

    assert decision.status == (
        SCHEDULE_STATUS_NO_PRIOR_CYCLE
    )
    assert decision.due
    assert decision.phase == "opening"


def test_opening_phase_waits_one_minute() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 30, 30),
        last_cycle_started_at=at(9, 30),
        last_cycle_id="cycle-opening",
    )

    assert decision.status == SCHEDULE_STATUS_NOT_DUE
    assert not decision.due
    assert decision.interval == timedelta(minutes=1)
    assert decision.next_due_at == at(9, 31)


def test_opening_phase_is_due_after_one_minute() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 31),
        last_cycle_started_at=at(9, 30),
    )

    assert decision.status == SCHEDULE_STATUS_DUE
    assert decision.due


def test_phase_transition_uses_prior_cycle_cadence() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 40, 30),
        last_cycle_started_at=at(9, 39, 30),
    )

    assert decision.phase == "early"
    assert decision.interval == timedelta(minutes=1)
    assert decision.status == SCHEDULE_STATUS_DUE


def test_early_phase_uses_five_minutes() -> None:
    not_due = evaluate_watchlist_cycle_schedule(
        now=at(9, 44, 59),
        last_cycle_started_at=at(9, 40),
    )
    due = evaluate_watchlist_cycle_schedule(
        now=at(9, 45),
        last_cycle_started_at=at(9, 40),
    )

    assert not_due.status == SCHEDULE_STATUS_NOT_DUE
    assert due.status == SCHEDULE_STATUS_DUE
    assert due.interval == timedelta(minutes=5)


def test_regular_phase_uses_ten_minutes() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(10, 40),
        last_cycle_started_at=at(10, 30),
    )

    assert decision.status == SCHEDULE_STATUS_DUE
    assert decision.phase == "regular"
    assert decision.interval == timedelta(minutes=10)


def test_output_schedule_uses_latest_cycle_in_current_session(
    tmp_path: Path,
) -> None:
    write_cycle(
        tmp_path,
        cycle_id="cycle-20260804-093000-old00001",
        started_at=at(9, 30),
    )
    write_cycle(
        tmp_path,
        cycle_id="cycle-20260804-093500-new00001",
        started_at=at(9, 35),
    )
    write_cycle(
        tmp_path,
        cycle_id="cycle-20260803-155000-yday0001",
        started_at=datetime(
            2026,
            8,
            3,
            15,
            50,
            tzinfo=EASTERN,
        ),
    )

    decision = evaluate_output_watchlist_cycle_schedule(
        tmp_path,
        now=at(9, 35, 30),
    )

    assert decision.last_cycle_id == (
        "cycle-20260804-093500-new00001"
    )
    assert decision.status == SCHEDULE_STATUS_NOT_DUE


def test_missing_output_directory_counts_as_no_prior_cycle(
    tmp_path: Path,
) -> None:
    decision = evaluate_output_watchlist_cycle_schedule(
        tmp_path / "missing",
        now=at(9, 30),
    )

    assert decision.status == (
        SCHEDULE_STATUS_NO_PRIOR_CYCLE
    )
    assert decision.due


def test_checker_reports_closed_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = wl_check_cycle_due.main(
        [
            "--output-dir",
            str(tmp_path),
            "--at",
            "2026-08-04T19:42:00-04:00",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert (
        "Decision          : OUTSIDE_SESSION"
        in captured.out
    )
    assert "Cycle due         : no" in captured.out
    assert (
        "Market timezone   : America/New_York"
        in captured.out
    )
    assert "Session           : 09:30 - 16:00 EDT" in (
        captured.out
    )


def test_chicago_open_converts_to_new_york_market_time() -> None:
    chicago_open = datetime(
        2026,
        8,
        4,
        8,
        30,
        tzinfo=CENTRAL,
    )

    decision = evaluate_watchlist_cycle_schedule(
        now=chicago_open
    )

    assert decision.status == (
        SCHEDULE_STATUS_NO_PRIOR_CYCLE
    )
    assert decision.evaluated_at == at(9, 30)
    assert decision.evaluated_at.tzinfo == EASTERN


def test_chicago_cycle_record_is_compared_in_market_time() -> None:
    last_cycle_chicago = datetime(
        2026,
        8,
        4,
        8,
        30,
        tzinfo=CENTRAL,
    )

    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 30, 30),
        last_cycle_started_at=last_cycle_chicago,
    )

    assert decision.status == SCHEDULE_STATUS_NOT_DUE
    assert decision.last_cycle_started_at == at(9, 30)
    assert decision.next_due_at == at(9, 31)


def test_winter_conversion_uses_est() -> None:
    chicago_open = datetime(
        2026,
        1,
        5,
        8,
        30,
        tzinfo=CENTRAL,
    )

    decision = evaluate_watchlist_cycle_schedule(
        now=chicago_open
    )

    assert decision.evaluated_at.hour == 9
    assert decision.evaluated_at.minute == 30
    assert decision.evaluated_at.tzname() == "EST"
    assert decision.session_start.tzname() == "EST"


def test_formatted_decision_names_market_timezone() -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=at(9, 30)
    )

    lines = format_watchlist_cycle_schedule_decision(
        decision
    )

    assert (
        "Market timezone   : America/New_York"
        in lines
    )
    assert "Session           : 09:30 - 16:00 EDT" in lines


def test_cycle_id_uses_new_york_market_clock() -> None:
    chicago_time = datetime(
        2026,
        8,
        4,
        8,
        30,
        tzinfo=CENTRAL,
    )

    cycle_id = generate_cycle_id(
        started_at=chicago_time,
        unique_suffix="a1b2c3d4",
    )

    assert cycle_id == (
        "cycle-20260804-093000-a1b2c3d4"
    )
