import json
from pathlib import Path

import pytest

import wl_list_plans
from watchlist_plan_index import (
    discover_watchlist_plans,
)


def write_run_record(
    path: Path,
    *,
    created_at: str,
    mode: str = "replace",
    symbols: list[str] | None = None,
    submitted: bool = False,
    return_code: int | None = None,
    source_plan_file: str | None = None,
) -> Path:
    if symbols is None:
        symbols = [
            "HYFM",
            "EZRA",
            "NVDA",
        ]

    scanner_command = {
        "add": "add_wl_symbols",
        "replace": "replace_wl_symbols",
    }[mode]

    record = {
        "created_at": created_at,
        "mode": mode,
        "scanner_command": scanner_command,
        "submitted": submitted,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "command": [
            "mb-scan-command",
            scanner_command,
            "--symbols",
            *symbols,
        ],
        "return_code": return_code,
        "scanner_preflight": None,
        "source_plan_file": source_plan_file,
        "source_plan_created_at": None,
    }

    path.write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    return path


def test_plan_is_marked_applied(
    tmp_path: Path,
) -> None:
    plan_path = write_run_record(
        tmp_path
        / "2026-08-03-120000-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:00:00-05:00"
        ),
    )

    application_path = write_run_record(
        tmp_path
        / "2026-08-03-121000-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:10:00-05:00"
        ),
        submitted=True,
        return_code=0,
        source_plan_file=str(
            plan_path.resolve()
        ),
    )

    index = discover_watchlist_plans(
        tmp_path
    )

    assert len(index.plans) == 1

    plan = index.plans[0]

    assert plan.applied
    assert plan.status == "APPLIED"
    assert plan.applied_run_path == (
        application_path.resolve()
    )
    assert plan.applied_at == (
        "2026-08-03T12:10:00-05:00"
    )


def test_derived_dry_run_is_not_a_plan(
    tmp_path: Path,
) -> None:
    plan_path = write_run_record(
        tmp_path
        / "2026-08-03-120000-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:00:00-05:00"
        ),
    )

    write_run_record(
        tmp_path
        / "2026-08-03-120500-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:05:00-05:00"
        ),
        source_plan_file=str(
            plan_path.resolve()
        ),
    )

    index = discover_watchlist_plans(
        tmp_path
    )

    assert len(index.plans) == 1
    assert index.plans[0].plan_path == (
        plan_path.resolve()
    )


def test_plans_are_sorted_newest_first(
    tmp_path: Path,
) -> None:
    write_run_record(
        tmp_path
        / "2026-08-03-120000-wl-add-run.json",
        created_at=(
            "2026-08-03T12:00:00-05:00"
        ),
        mode="add",
        symbols=["AAA"],
    )

    write_run_record(
        tmp_path
        / "2026-08-03-130000-wl-replace-run.json",
        created_at=(
            "2026-08-03T13:00:00-05:00"
        ),
        symbols=["BBB"],
    )

    index = discover_watchlist_plans(
        tmp_path
    )

    assert [
        plan.symbols
        for plan in index.plans
    ] == [
        ("BBB",),
        ("AAA",),
    ]


def test_failed_application_does_not_mark_applied(
    tmp_path: Path,
) -> None:
    plan_path = write_run_record(
        tmp_path
        / "2026-08-03-120000-wl-add-run.json",
        created_at=(
            "2026-08-03T12:00:00-05:00"
        ),
        mode="add",
        symbols=["TEST"],
    )

    write_run_record(
        tmp_path
        / "2026-08-03-121000-wl-add-run.json",
        created_at=(
            "2026-08-03T12:10:00-05:00"
        ),
        mode="add",
        symbols=["TEST"],
        submitted=True,
        return_code=1,
        source_plan_file=str(
            plan_path.resolve()
        ),
    )

    index = discover_watchlist_plans(
        tmp_path
    )

    assert len(index.plans) == 1
    assert not index.plans[0].applied
    assert index.plans[0].status == (
        "REVIEWED"
    )


def test_list_plans_pending_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending_path = write_run_record(
        tmp_path
        / "2026-08-03-130000-wl-add-run.json",
        created_at=(
            "2026-08-03T13:00:00-05:00"
        ),
        mode="add",
        symbols=["PENDING"],
    )

    applied_path = write_run_record(
        tmp_path
        / "2026-08-03-120000-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:00:00-05:00"
        ),
        symbols=["APPLIED"],
    )

    write_run_record(
        tmp_path
        / "2026-08-03-121000-wl-replace-run.json",
        created_at=(
            "2026-08-03T12:10:00-05:00"
        ),
        symbols=["APPLIED"],
        submitted=True,
        return_code=0,
        source_plan_file=str(
            applied_path.resolve()
        ),
    )

    exit_code = wl_list_plans.main(
        [
            "--output-dir",
            str(tmp_path),
            "--pending-only",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "[REVIEWED]" in captured.out
    assert "PENDING" in captured.out
    assert str(pending_path.resolve()) in (
        captured.out
    )
    assert "APPLIED" not in captured.out
