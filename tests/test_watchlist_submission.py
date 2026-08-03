import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchlist_submission import (
    RECORD_ORIGIN_DIRECT_SUBMISSION,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
    build_watchlist_command,
    normalize_symbols,
    submit_watchlist_symbols,
)
from scanner_preflight import ScannerPreflightResult



CREATED_AT = datetime(
    2026,
    8,
    2,
    19,
    0,
    tzinfo=timezone.utc,
)


def test_normalize_symbols() -> None:
    assert normalize_symbols(
        [
            "amd NVDA",
            "pltr,AMD",
            " nvda ",
        ]
    ) == [
        "AMD",
        "NVDA",
        "PLTR",
    ]


def test_build_watchlist_command(
    tmp_path: Path,
) -> None:
    command = build_watchlist_command(
        mode="add",
        symbols=["amd", "NVDA"],
        wait=15.0,
        root=tmp_path,
    )

    assert command == (
        "mb-scan-command",
        "add_wl_symbols",
        "--symbols",
        "AMD",
        "NVDA",
        "--root",
        str(tmp_path.resolve()),
        "--wait",
        "15.0",
    )


def test_dry_run_does_not_publish(
    tmp_path: Path,
) -> None:
    def unexpected_finder(name: str) -> str:
        raise AssertionError(
            f"Executable lookup was unexpected: {name}"
        )

    def unexpected_runner(*args, **kwargs):
        raise AssertionError(
            "Subprocess execution was unexpected."
        )

    result = submit_watchlist_symbols(
        mode="replace",
        symbols=["AAPL", "MSFT"],
        submit=False,
        output_dir=tmp_path,
        executable_finder=unexpected_finder,
        runner=unexpected_runner,
        created_at=CREATED_AT,
    )

    assert not result.submitted
    assert result.return_code is None
    assert result.successful
    assert result.symbols == (
        "AAPL",
        "MSFT",
    )

    record = json.loads(
        result.run_record_path.read_text(
            encoding="utf-8"
        )
    )
    assert record["record_origin"] == (
        RECORD_ORIGIN_DIRECT_SUBMISSION
    )

    assert record["submitted"] is False
    assert record["scanner_command"] == (
        "replace_wl_symbols"
    )
    assert record["symbols"] == [
        "AAPL",
        "MSFT",
    ]


def test_live_submission_uses_resolved_executable(
    tmp_path: Path,
) -> None:
    calls = []

    class Completed:
        returncode = 0

    preflight_calls = []

    def fake_preflight(
        *,
        root: Path | None,
    ) -> ScannerPreflightResult:
        preflight_calls.append(root)

        return ScannerPreflightResult(
            root=tmp_path,
            ready=True,
            status="HEALTHY",
            detail=(
                "Scanner is healthy, idle, running, "
                "and not paused."
            ),
            loop_state="idle",
            running=True,
            paused=False,
            age_seconds=1.0,
        )

    def fake_finder(name: str) -> str:
        assert name == "mb-scan-command"
        return r"C:\tools\mb-scan-command.exe"

    def fake_runner(
        command,
        *,
        check: bool,
        text: bool,
    ):
        calls.append(
            {
                "command": command,
                "check": check,
                "text": text,
            }
        )
        return Completed()

    result = submit_watchlist_symbols(
        mode="add",
        symbols=["IBM", "ORCL"],
        submit=True,
        wait=30.0,
        output_dir=tmp_path,
        executable_finder=fake_finder,
        runner=fake_runner,
        created_at=CREATED_AT,
        preflight_checker=fake_preflight,
    )

    assert result.submitted
    assert result.return_code == 0
    assert result.successful

    assert preflight_calls == [None]
    assert result.preflight is not None
    assert result.preflight.ready
    assert result.preflight.status == "HEALTHY"

    assert calls == [
        {
            "command": [
                r"C:\tools\mb-scan-command.exe",
                "add_wl_symbols",
                "--symbols",
                "IBM",
                "ORCL",
                "--wait",
                "30.0",
            ],
            "check": False,
            "text": True,
        }
    ]


def test_invalid_mode_is_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported Watchlist mode",
    ):
        submit_watchlist_symbols(
            mode="invalid",
            symbols=["TEST"],
            output_dir=tmp_path,
        )


def test_empty_symbol_list_is_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="At least one Watchlist symbol",
    ):
        submit_watchlist_symbols(
            mode="add",
            symbols=[],
            output_dir=tmp_path,
        )


def test_negative_wait_is_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot be negative",
    ):
        submit_watchlist_symbols(
            mode="add",
            symbols=["TEST"],
            wait=-1,
            output_dir=tmp_path,
        )


def test_failed_preflight_blocks_live_command(
    tmp_path: Path,
) -> None:
    def failed_preflight(
        *,
        root: Path | None,
    ) -> ScannerPreflightResult:
        return ScannerPreflightResult(
            root=tmp_path,
            ready=False,
            status="STOPPED",
            detail=(
                "Scanner is not ready for Watchlist "
                "submission."
            ),
            loop_state="stopped",
            running=False,
            paused=False,
            age_seconds=120.0,
        )

    def unexpected_finder(name: str) -> str:
        raise AssertionError(
            "Executable lookup must not occur "
            "after failed preflight."
        )

    def unexpected_runner(*args, **kwargs):
        raise AssertionError(
            "Command execution must not occur "
            "after failed preflight."
        )

    with pytest.raises(
        RuntimeError,
        match="Scanner preflight failed",
    ):
        submit_watchlist_symbols(
            mode="add",
            symbols=["TEST"],
            submit=True,
            output_dir=tmp_path,
            preflight_checker=failed_preflight,
            executable_finder=unexpected_finder,
            runner=unexpected_runner,
        )


def test_invalid_record_origin_is_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Unsupported Watchlist record origin"
        ),
    ):
        submit_watchlist_symbols(
            mode="add",
            symbols=["AAPL"],
            submit=False,
            output_dir=tmp_path,
            record_origin="unknown_origin",
        )


def test_cycle_id_is_saved_and_used_in_filename(
    tmp_path: Path,
) -> None:
    cycle_id = (
        "cycle-20260803-174600-a1b2c3d4"
    )

    result = submit_watchlist_symbols(
        mode="replace",
        symbols=["AAPL", "MSFT"],
        submit=False,
        output_dir=tmp_path,
        created_at=CREATED_AT,
        record_origin=(
            RECORD_ORIGIN_WATCHLIST_CYCLE
        ),
        cycle_id=cycle_id,
    )

    assert result.run_record_path.name == (
        f"{cycle_id}-wl-replace-run.json"
    )

    record = json.loads(
        result.run_record_path.read_text(
            encoding="utf-8"
        )
    )

    assert record["cycle_id"] == cycle_id
    assert record["record_origin"] == (
        RECORD_ORIGIN_WATCHLIST_CYCLE
    )