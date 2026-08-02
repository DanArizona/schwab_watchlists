import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchlist_submission import (
    build_watchlist_command,
    normalize_symbols,
    submit_watchlist_symbols,
)


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
    )

    assert result.submitted
    assert result.return_code == 0
    assert result.successful

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
