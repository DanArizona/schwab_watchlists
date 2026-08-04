from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import run_watchlist_cycle
from watchlist_cycle_schedule import (
    evaluate_watchlist_cycle_schedule,
)

CENTRAL = ZoneInfo("America/Chicago")


def test_require_due_stops_before_schwab_access(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = evaluate_watchlist_cycle_schedule(
        now=datetime(2026, 8, 4, 18, 42, tzinfo=CENTRAL)
    )

    monkeypatch.setattr(
        run_watchlist_cycle,
        "evaluate_output_watchlist_cycle_schedule",
        lambda output_dir: decision,
    )

    def unexpected_ecfg_path(path):
        raise AssertionError("Schwab configuration access was unexpected.")

    monkeypatch.setattr(
        run_watchlist_cycle,
        "resolve_ecfg_path",
        unexpected_ecfg_path,
    )

    exit_code = run_watchlist_cycle.main(
        [
            "--require-due",
            "--mode",
            "replace",
            "--output-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "Decision          : OUTSIDE_SESSION" in captured.out
    assert "No Watchlist cycle was started" in captured.out
    assert not list(tmp_path.glob("cycle-*.json"))


def test_require_due_cannot_be_used_with_replay(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_watchlist_cycle.main(
        [
            "--require-due",
            "--replay",
            "not-needed.json",
            "--mode",
            "replace",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--require-due cannot be used with --replay" in captured.err
