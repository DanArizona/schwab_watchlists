from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import run_watchlist_cycle
from candidate_filters import FilterSettings
from scanner_preflight import ScannerPreflightResult
from schwab_movers_source import load_schwab_movers_replay
from watchlist_cycle import run_schwab_movers_cycle
from watchlist_submission import (
    RECORD_ORIGIN_PLAN_APPLICATION,
    RECORD_ORIGIN_WATCHLIST_CYCLE,
    WatchlistSubmissionResult,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "schwab_movers_sample.json"
)
TIME_ZONE = timezone(timedelta(hours=-5))
BATCH_TIME = datetime(2026, 8, 4, 14, 5, 0, tzinfo=TIME_ZONE)


def load_batch():
    return load_schwab_movers_replay(
        FIXTURE_PATH,
        market="NASDAQ",
        sort_name="PERCENT_CHANGE_UP",
        frequency=5,
        as_of=BATCH_TIME,
    )


def test_cycle_cli_replay_rejects_submit(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = run_watchlist_cycle.main(
        [
            "--replay",
            str(FIXTURE_PATH),
            "--mode",
            "replace",
            "--submit",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--submit cannot be used with --replay" in captured.err
    assert "permanently dry-run only" in captured.err
    assert not list(tmp_path.iterdir())


def test_apply_frozen_cycle_plan_rejects_cycle_mismatch(
    tmp_path: Path,
) -> None:
    cycle_result = run_schwab_movers_cycle(
        batch=load_batch(),
        filter_settings=FilterSettings(
            min_price=1.0,
            min_volume=1_000_000,
            min_percent_change=5.0,
            max_results=2,
        ),
        mode="replace",
        output_dir=tmp_path,
        input_mode="replay",
        replay_path=FIXTURE_PATH,
        cycle_id="cycle-20260804-140500-a1b2c3d4",
        started_at=BATCH_TIME,
        completed_at=BATCH_TIME,
    )

    assert cycle_result.watchlist_plan is not None

    def unexpected_submitter(**kwargs):
        raise AssertionError("Submission was unexpected.")

    with pytest.raises(
        ValueError,
        match="cycle_id does not match",
    ):
        run_watchlist_cycle.apply_frozen_cycle_plan(
            plan_path=(
                cycle_result.watchlist_plan.run_record_path
            ),
            expected_cycle_id=(
                "cycle-20260804-140500-deadbeef"
            ),
            output_dir=tmp_path,
            root=None,
            wait=30.0,
            submitter=unexpected_submitter,
        )


def test_cycle_cli_live_submit_applies_exact_frozen_plan(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ecfg_path = tmp_path / "secure_schwabdev.ecfg"
    ecfg_path.write_text("test", encoding="utf-8")
    scanner_root = tmp_path / "scanctrl"

    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    calls: dict[str, object] = {}

    def fake_make_secure_schwab_client(
        path: Path,
        password: str,
        *,
        timeout: int,
    ) -> FakeClient:
        calls["client_path"] = path
        calls["password"] = password
        calls["timeout"] = timeout
        return client

    def fake_fetch_schwab_movers(
        received_client,
        *,
        market: str,
        sort_name: str,
        frequency: int,
    ):
        calls["fetch_client"] = received_client
        calls["market"] = market
        calls["sort"] = sort_name
        calls["frequency"] = frequency

        return replace(
            load_batch(),
            request_url="https://example.test/marketdata/v1/movers",
            status_code=200,
        )

    def fake_submit_watchlist_symbols(**kwargs):
        calls["submit_kwargs"] = kwargs

        source_plan_path = Path(kwargs["source_plan_path"])
        plan_record = json.loads(
            source_plan_path.read_text(encoding="utf-8")
        )

        assert plan_record["record_origin"] == (
            RECORD_ORIGIN_WATCHLIST_CYCLE
        )
        assert plan_record["submitted"] is False
        assert plan_record["symbols"] == ["TSTA", "TSTB"]
        assert kwargs["symbols"] == ("TSTA", "TSTB")
        assert kwargs["cycle_id"] == plan_record["cycle_id"]
        assert kwargs["record_origin"] == (
            RECORD_ORIGIN_PLAN_APPLICATION
        )
        assert kwargs["submit"] is True

        application_path = (
            tmp_path
            / "2026-08-04-14-05-05-wl-replace-run.json"
        )
        application_path.write_text(
            json.dumps(
                {
                    "record_origin": RECORD_ORIGIN_PLAN_APPLICATION,
                    "cycle_id": kwargs["cycle_id"],
                    "submitted": True,
                    "return_code": 0,
                    "source_plan_file": str(source_plan_path),
                }
            ),
            encoding="utf-8",
        )

        preflight = ScannerPreflightResult(
            root=scanner_root.resolve(),
            ready=True,
            status="HEALTHY",
            detail="ready",
            loop_state="idle",
            running=True,
            paused=False,
            age_seconds=1.0,
        )

        return WatchlistSubmissionResult(
            mode=kwargs["mode"],
            scanner_command="replace_wl_symbols",
            symbols=tuple(kwargs["symbols"]),
            command=("mb-scan-command", "replace_wl_symbols"),
            submitted=True,
            return_code=0,
            run_record_path=application_path,
            preflight=preflight,
        )

    monkeypatch.setattr(
        run_watchlist_cycle.getpass,
        "getpass",
        lambda prompt: "test-password",
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "make_secure_schwab_client",
        fake_make_secure_schwab_client,
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "fetch_schwab_movers",
        fake_fetch_schwab_movers,
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "submit_watchlist_symbols",
        fake_submit_watchlist_symbols,
    )

    exit_code = run_watchlist_cycle.main(
        [
            "--ecfg",
            str(ecfg_path),
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
            "replace",
            "--submit",
            "--root",
            str(scanner_root),
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "Submission       : LIVE" in captured.out
    assert "Applying exact frozen cycle plan" in captured.out
    assert "Scanner preflight       : READY" in captured.out
    assert "Frozen cycle plan was reported as processed" in captured.out
    assert client.closed

    submit_kwargs = calls["submit_kwargs"]
    assert submit_kwargs["root"] == scanner_root
    assert submit_kwargs["output_dir"] == tmp_path

    candidate_files = list(
        tmp_path.glob(
            "cycle-*-movers-nasdaq-percent_change_up-run.json"
        )
    )
    assert len(candidate_files) == 1

    candidate_record = json.loads(
        candidate_files[0].read_text(encoding="utf-8")
    )
    assert candidate_record["watchlist_submit_requested"] is True

    plan_files = list(
        tmp_path.glob("cycle-*-wl-replace-run.json")
    )
    assert len(plan_files) == 1

    plan_record = json.loads(
        plan_files[0].read_text(encoding="utf-8")
    )
    assert plan_record["record_origin"] == (
        RECORD_ORIGIN_WATCHLIST_CYCLE
    )
    assert plan_record["submitted"] is False
