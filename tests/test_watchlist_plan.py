import json
from pathlib import Path

import pytest

import wl_apply_plan
from watchlist_plan import load_watchlist_plan


def write_plan(
    path: Path,
    *,
    mode: str = "replace",
    symbols: list[str] | None = None,
    submitted: bool = False,
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
    }.get(mode, "invalid_command")

    record = {
        "created_at": "2026-08-03T12:00:37-05:00",
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
        "return_code": None,
        "scanner_preflight": None,
    }

    path.write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    return path


def test_load_valid_watchlist_plan(
    tmp_path: Path,
) -> None:
    plan_path = write_plan(
        tmp_path / "plan.json"
    )

    plan = load_watchlist_plan(plan_path)

    assert plan.source_path == plan_path.resolve()
    assert plan.mode == "replace"
    assert plan.scanner_command == (
        "replace_wl_symbols"
    )
    assert plan.symbols == (
        "HYFM",
        "EZRA",
        "NVDA",
    )


def test_submitted_record_is_rejected(
    tmp_path: Path,
) -> None:
    plan_path = write_plan(
        tmp_path / "submitted.json",
        submitted=True,
    )

    with pytest.raises(
        ValueError,
        match="unsubmitted dry-run",
    ):
        load_watchlist_plan(plan_path)


def test_invalid_mode_is_rejected(
    tmp_path: Path,
) -> None:
    plan_path = write_plan(
        tmp_path / "invalid-mode.json",
        mode="delete",
    )

    with pytest.raises(
        ValueError,
        match="Unsupported Watchlist plan mode",
    ):
        load_watchlist_plan(plan_path)


def test_empty_symbol_list_is_rejected(
    tmp_path: Path,
) -> None:
    plan_path = write_plan(
        tmp_path / "empty.json",
        symbols=[],
    )

    with pytest.raises(
        ValueError,
        match="contains no symbols",
    ):
        load_watchlist_plan(plan_path)


def test_apply_plan_dry_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = write_plan(
        tmp_path / "reviewed-plan.json"
    )

    output_dir = tmp_path / "output"

    exit_code = wl_apply_plan.main(
        [
            "--plan",
            str(plan_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "Submission       : DRY RUN" in captured.out
    assert "HYFM EZRA NVDA" in captured.out
    assert "No command was published." in captured.out

    run_files = list(
        output_dir.glob(
            "*-wl-replace-run.json"
        )
    )

    assert len(run_files) == 1

    record = json.loads(
        run_files[0].read_text(
            encoding="utf-8"
        )
    )

    assert record["submitted"] is False
    assert record["mode"] == "replace"
    assert record["symbols"] == [
        "HYFM",
        "EZRA",
        "NVDA",
    ]

    assert record["source_plan_file"] == str(
        plan_path.resolve()
    )
    assert record["source_plan_created_at"] == (
        "2026-08-03T12:00:37-05:00"
    )