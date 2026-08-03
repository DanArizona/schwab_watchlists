from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import wl_schwab_movers

from watchlist_submission import (
    RECORD_ORIGIN_SCHWAB_MOVERS,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "schwab_movers_sample.json"
)


def fail_if_called(
    *args: Any,
    **kwargs: Any,
) -> Any:
    raise AssertionError(
        "Live Schwab authentication was attempted "
        "during replay."
    )


def test_replay_watchlist_dry_run_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Exercise the complete replay workflow without external services.

    The test covers:

    - replay-file loading;
    - local Movers ordering;
    - candidate conversion;
    - scalar filtering;
    - result limiting;
    - candidate output files;
    - Watchlist dry-run preparation;
    - Watchlist run-record creation.
    """

    monkeypatch.setattr(
        wl_schwab_movers.getpass,
        "getpass",
        fail_if_called,
    )
    monkeypatch.setattr(
        wl_schwab_movers,
        "make_secure_schwab_client",
        fail_if_called,
    )

    exit_code = wl_schwab_movers.main(
        [
            "--replay",
            str(FIXTURE_PATH),
            "--market",
            "NASDAQ",
            "--sort",
            "PERCENT_CHANGE_UP",
            "--min-price",
            "1",
            "--min-volume",
            "1000000",
            "--min-percent-change",
            "5",
            "--limit",
            "2",
            "--mode",
            "add",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""

    assert (
        "Input source      : SAVED RESPONSE REPLAY"
        in captured.out
    )
    assert (
        "Watchlist action : ADD (DRY RUN)"
        in captured.out
    )
    assert "Accepted records : 2" in captured.out
    assert "Rejected records : 5" in captured.out
    assert "Submission       : DRY RUN" in captured.out
    assert "TSTA TSTB" in captured.out
    assert (
        "No Watchlist command was published."
        in captured.out
    )
    assert "ecfg password:" not in captured.out

    symbol_files = list(
        tmp_path.glob(
            "*-movers-nasdaq-"
            "percent_change_up-replay-symbols.txt"
        )
    )

    assert len(symbol_files) == 1
    assert symbol_files[0].read_text(
        encoding="utf-8"
    ) == "TSTA\nTSTB\n"

    candidate_run_files = list(
        tmp_path.glob(
            "*-movers-nasdaq-"
            "percent_change_up-replay-run.json"
        )
    )

    assert len(candidate_run_files) == 1

    candidate_record = json.loads(
        candidate_run_files[0].read_text(
            encoding="utf-8"
        )
    )

    assert candidate_record["input_mode"] == "replay"
    assert candidate_record["pipeline_source"] == (
        "schwab_movers"
    )
    assert candidate_record["input_count"] == 7
    assert candidate_record["accepted_count"] == 2
    assert candidate_record["rejected_count"] == 5
    assert candidate_record["accepted_symbols"] == [
        "TSTA",
        "TSTB",
    ]

    assert candidate_record["watchlist_action"] == (
        "add_wl_symbols"
    )
    assert candidate_record["watchlist_mode"] == "add"
    assert (
        candidate_record[
            "watchlist_submit_requested"
        ]
        is False
    )

    watchlist_run_files = list(
        tmp_path.glob("*-wl-add-run.json")
    )

    assert len(watchlist_run_files) == 1

    watchlist_record = json.loads(
        watchlist_run_files[0].read_text(
            encoding="utf-8"
        )
    )

    assert watchlist_record["mode"] == "add"
    assert watchlist_record["scanner_command"] == (
        "add_wl_symbols"
    )
    assert watchlist_record["submitted"] is False
    assert watchlist_record["record_origin"] == (
        RECORD_ORIGIN_SCHWAB_MOVERS
    )
    assert watchlist_record["symbol_count"] == 2
    assert watchlist_record["symbols"] == [
        "TSTA",
        "TSTB",
    ]
    assert watchlist_record["return_code"] is None

    assert watchlist_record["command"] == [
        "mb-scan-command",
        "add_wl_symbols",
        "--symbols",
        "TSTA",
        "TSTB",
        "--wait",
        "30.0",
    ]


def test_replay_live_submission_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Replay data must never be published as a live command."""

    monkeypatch.setattr(
        wl_schwab_movers.getpass,
        "getpass",
        fail_if_called,
    )
    monkeypatch.setattr(
        wl_schwab_movers,
        "make_secure_schwab_client",
        fail_if_called,
    )

    exit_code = wl_schwab_movers.main(
        [
            "--replay",
            str(FIXTURE_PATH),
            "--mode",
            "add",
            "--submit",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert (
        "--submit cannot be used with --replay"
        in captured.err
    )

    assert list(tmp_path.iterdir()) == []
