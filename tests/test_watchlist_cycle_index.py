import json
from pathlib import Path

import pytest

import wl_list_cycles
from watchlist_cycle_index import (
    CYCLE_STATE_APPLIED,
    CYCLE_STATE_APPLICATION_FAILED,
    CYCLE_STATE_NO_CANDIDATES,
    CYCLE_STATE_PLAN_CREATED,
    CYCLE_STATE_PREVIEWED,
    discover_watchlist_cycles,
)


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return path


def write_cycle_record(
    output_dir: Path,
    *,
    cycle_id: str,
    started_at: str = "2026-08-04T12:27:02-05:00",
    status: str = "plan_created",
    symbols: list[str] | None = None,
    plan_path: Path | None = None,
) -> Path:
    if symbols is None:
        symbols = ["AMIX", "QNME", "ADGM"]

    return write_json(
        output_dir / f"{cycle_id}-cycle-run.json",
        {
            "cycle_id": cycle_id,
            "started_at": started_at,
            "completed_at": started_at,
            "status": status,
            "candidate_source": "schwab_movers",
            "strategy_name": "movers_demo_v1",
            "input_mode": "api",
            "watchlist_mode": "replace",
            "input_count": 10,
            "accepted_count": len(symbols),
            "rejected_count": 10 - len(symbols),
            "accepted_symbols": symbols,
            "candidate_raw_file": str(
                output_dir / f"{cycle_id}-raw.json"
            ),
            "candidate_symbols_file": str(
                output_dir / f"{cycle_id}-symbols.txt"
            ),
            "candidate_run_file": str(
                output_dir / f"{cycle_id}-run.json"
            ),
            "watchlist_plan_file": (
                str(plan_path.resolve())
                if plan_path is not None
                else None
            ),
            "replay_file": None,
        },
    )


def write_plan(path: Path, cycle_id: str) -> Path:
    return write_json(
        path,
        {
            "created_at": "2026-08-04T12:27:02-05:00",
            "record_origin": "watchlist_cycle",
            "cycle_id": cycle_id,
            "mode": "replace",
            "scanner_command": "replace_wl_symbols",
            "submitted": False,
            "symbol_count": 3,
            "symbols": ["AMIX", "QNME", "ADGM"],
            "command": [],
            "return_code": None,
            "scanner_preflight": None,
            "source_plan_file": None,
            "source_plan_created_at": None,
        },
    )


def write_derived_record(
    path: Path,
    *,
    plan_path: Path,
    created_at: str,
    origin: str,
    submitted: bool,
    return_code: int | None,
) -> Path:
    return write_json(
        path,
        {
            "created_at": created_at,
            "record_origin": origin,
            "cycle_id": "cycle-20260804-122702-99056cf0",
            "mode": "replace",
            "scanner_command": "replace_wl_symbols",
            "submitted": submitted,
            "symbol_count": 3,
            "symbols": ["AMIX", "QNME", "ADGM"],
            "command": [],
            "return_code": return_code,
            "scanner_preflight": None,
            "source_plan_file": str(plan_path.resolve()),
            "source_plan_created_at": (
                "2026-08-04T12:27:02-05:00"
            ),
        },
    )


def make_cycle_with_plan(
    tmp_path: Path,
) -> tuple[str, Path]:
    cycle_id = "cycle-20260804-122702-99056cf0"
    plan_path = write_plan(
        tmp_path / f"{cycle_id}-wl-replace-run.json",
        cycle_id,
    )
    write_cycle_record(
        tmp_path,
        cycle_id=cycle_id,
        plan_path=plan_path,
    )
    return cycle_id, plan_path


def test_cycle_with_plan_is_plan_created(
    tmp_path: Path,
) -> None:
    cycle_id, plan_path = make_cycle_with_plan(
        tmp_path
    )

    index = discover_watchlist_cycles(tmp_path)

    assert len(index.cycles) == 1
    cycle = index.cycles[0]
    assert cycle.cycle_id == cycle_id
    assert cycle.state == CYCLE_STATE_PLAN_CREATED
    assert cycle.plan_path == plan_path.resolve()
    assert cycle.pending


def test_preview_marks_cycle_previewed(
    tmp_path: Path,
) -> None:
    _, plan_path = make_cycle_with_plan(tmp_path)
    preview_path = write_derived_record(
        tmp_path / "2026-08-04-12-34-09-wl-replace-run.json",
        plan_path=plan_path,
        created_at="2026-08-04T12:34:09-05:00",
        origin="plan_preview",
        submitted=False,
        return_code=None,
    )

    cycle = discover_watchlist_cycles(
        tmp_path
    ).cycles[0]

    assert cycle.state == CYCLE_STATE_PREVIEWED
    assert cycle.preview_run_path == preview_path.resolve()
    assert cycle.previewed_at == "2026-08-04T12:34:09-05:00"
    assert cycle.pending


def test_successful_application_marks_cycle_applied(
    tmp_path: Path,
) -> None:
    _, plan_path = make_cycle_with_plan(tmp_path)
    application_path = write_derived_record(
        tmp_path / "2026-08-04-12-42-53-wl-replace-run.json",
        plan_path=plan_path,
        created_at="2026-08-04T12:42:53-05:00",
        origin="plan_application",
        submitted=True,
        return_code=0,
    )

    cycle = discover_watchlist_cycles(
        tmp_path
    ).cycles[0]

    assert cycle.state == CYCLE_STATE_APPLIED
    assert cycle.applied
    assert not cycle.pending
    assert cycle.application_run_path == (
        application_path.resolve()
    )
    assert cycle.applied_at == "2026-08-04T12:42:53-05:00"
    assert cycle.application_return_code == 0


def test_failed_application_is_reported(
    tmp_path: Path,
) -> None:
    _, plan_path = make_cycle_with_plan(tmp_path)
    write_derived_record(
        tmp_path / "2026-08-04-12-42-53-wl-replace-run.json",
        plan_path=plan_path,
        created_at="2026-08-04T12:42:53-05:00",
        origin="plan_application",
        submitted=True,
        return_code=1,
    )

    cycle = discover_watchlist_cycles(
        tmp_path
    ).cycles[0]

    assert cycle.state == CYCLE_STATE_APPLICATION_FAILED
    assert cycle.pending
    assert cycle.application_return_code == 1


def test_no_candidate_cycle_is_reported(
    tmp_path: Path,
) -> None:
    cycle_id = "cycle-20260804-130000-empty000"
    write_cycle_record(
        tmp_path,
        cycle_id=cycle_id,
        status="no_candidates",
        symbols=[],
        plan_path=None,
    )

    cycle = discover_watchlist_cycles(
        tmp_path
    ).cycles[0]

    assert cycle.state == CYCLE_STATE_NO_CANDIDATES
    assert cycle.plan_path is None
    assert not cycle.pending


def test_list_cycles_pending_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending_id = "cycle-20260804-130000-pending1"
    pending_plan = write_plan(
        tmp_path / f"{pending_id}-wl-replace-run.json",
        pending_id,
    )
    write_cycle_record(
        tmp_path,
        cycle_id=pending_id,
        started_at="2026-08-04T13:00:00-05:00",
        plan_path=pending_plan,
    )

    applied_id = "cycle-20260804-120000-applied1"
    applied_plan = write_plan(
        tmp_path / f"{applied_id}-wl-replace-run.json",
        applied_id,
    )
    write_cycle_record(
        tmp_path,
        cycle_id=applied_id,
        started_at="2026-08-04T12:00:00-05:00",
        plan_path=applied_plan,
    )
    write_derived_record(
        tmp_path / "2026-08-04-12-10-00-wl-replace-run.json",
        plan_path=applied_plan,
        created_at="2026-08-04T12:10:00-05:00",
        origin="plan_application",
        submitted=True,
        return_code=0,
    )

    exit_code = wl_list_cycles.main(
        [
            "--output-dir",
            str(tmp_path),
            "--pending-only",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert pending_id in captured.out
    assert applied_id not in captured.out
    assert "[PLAN_CREATED]" in captured.out
