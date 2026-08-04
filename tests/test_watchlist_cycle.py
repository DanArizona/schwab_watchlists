from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_watchlist_cycle
from candidate_filters import FilterSettings
from schwab_movers_source import load_schwab_movers_replay
from watchlist_cycle import (
    CYCLE_STATUS_NO_CANDIDATES,
    CYCLE_STATUS_PLAN_CREATED,
    run_schwab_movers_cycle,
)
from watchlist_submission import RECORD_ORIGIN_WATCHLIST_CYCLE

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "schwab_movers_sample.json"
)
TIME_ZONE = timezone(timedelta(hours=-5))
STARTED_AT = datetime(2026, 8, 3, 18, 45, 0, tzinfo=TIME_ZONE)
COMPLETED_AT = datetime(2026, 8, 3, 18, 45, 2, tzinfo=TIME_ZONE)
CYCLE_ID = "cycle-20260803-184500-a1b2c3d4"


def load_batch():
    return load_schwab_movers_replay(
        FIXTURE_PATH,
        market="NASDAQ",
        sort_name="PERCENT_CHANGE_UP",
        frequency=5,
        as_of=STARTED_AT,
    )


def test_replay_cycle_creates_frozen_plan_and_audit_record(
    tmp_path: Path,
) -> None:
    result = run_schwab_movers_cycle(
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
        cycle_id=CYCLE_ID,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )

    assert result.status == CYCLE_STATUS_PLAN_CREATED
    assert result.plan_created
    assert result.pipeline_result.accepted_symbols == (
        "TSTA",
        "TSTB",
    )
    assert result.watchlist_plan is not None
    assert result.watchlist_plan.run_record_path.name == (
        f"{CYCLE_ID}-wl-replace-run.json"
    )
    assert result.cycle_record_path.name == (
        f"{CYCLE_ID}-cycle-run.json"
    )

    candidate_record = json.loads(
        result.candidate_outputs.run_json.read_text(
            encoding="utf-8"
        )
    )
    assert candidate_record["cycle_id"] == CYCLE_ID
    assert candidate_record["strategy_name"] == "movers_demo_v1"
    assert candidate_record["accepted_symbols"] == ["TSTA", "TSTB"]
    assert candidate_record["watchlist_submit_requested"] is False

    plan_record = json.loads(
        result.watchlist_plan.run_record_path.read_text(
            encoding="utf-8"
        )
    )
    assert plan_record["record_origin"] == (
        RECORD_ORIGIN_WATCHLIST_CYCLE
    )
    assert plan_record["cycle_id"] == CYCLE_ID
    assert plan_record["submitted"] is False
    assert plan_record["symbols"] == ["TSTA", "TSTB"]

    cycle_record = json.loads(
        result.cycle_record_path.read_text(
            encoding="utf-8"
        )
    )
    assert cycle_record["cycle_id"] == CYCLE_ID
    assert cycle_record["status"] == CYCLE_STATUS_PLAN_CREATED
    assert cycle_record["accepted_symbols"] == ["TSTA", "TSTB"]
    assert cycle_record["watchlist_plan_file"] == str(
        result.watchlist_plan.run_record_path
    )


def test_cycle_with_no_candidates_writes_audit_without_plan(
    tmp_path: Path,
) -> None:
    result = run_schwab_movers_cycle(
        batch=load_batch(),
        filter_settings=FilterSettings(min_price=1_000.0),
        mode="replace",
        output_dir=tmp_path,
        input_mode="replay",
        replay_path=FIXTURE_PATH,
        cycle_id=CYCLE_ID,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )

    assert result.status == CYCLE_STATUS_NO_CANDIDATES
    assert not result.plan_created
    assert result.watchlist_plan is None
    assert result.pipeline_result.accepted_symbols == ()
    assert not list(tmp_path.glob("*-wl-replace-run.json"))

    cycle_record = json.loads(
        result.cycle_record_path.read_text(
            encoding="utf-8"
        )
    )
    assert cycle_record["status"] == CYCLE_STATUS_NO_CANDIDATES
    assert cycle_record["accepted_count"] == 0
    assert cycle_record["watchlist_plan_file"] is None


def test_cycle_cli_replay_is_dry_run(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = run_watchlist_cycle.main(
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
    assert "Watchlist cycle" in captured.out
    assert "Accepted         : 2" in captured.out
    assert "TSTA TSTB" in captured.out
    assert "No Watchlist command was published." in captured.out
    assert len(list(tmp_path.glob("cycle-*-wl-add-run.json"))) == 1
    assert len(list(tmp_path.glob("cycle-*-cycle-run.json"))) == 1


def test_cycle_cli_live_api_is_dry_run(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ecfg_path = tmp_path / "secure_schwabdev.ecfg"
    ecfg_path.write_text("test", encoding="utf-8")

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

    exit_code = run_watchlist_cycle.main(
        [
            "--ecfg",
            str(ecfg_path),
            "--timeout",
            "17",
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
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert "Input source     : Live Schwab API" in captured.out
    assert "HTTP status      : 200" in captured.out
    assert "TSTA TSTB" in captured.out
    assert "No Watchlist command was published." in captured.out

    assert calls["client_path"] == ecfg_path.resolve()
    assert calls["password"] == "test-password"
    assert calls["timeout"] == 17
    assert calls["fetch_client"] is client
    assert calls["market"] == "NASDAQ"
    assert calls["sort"] == "PERCENT_CHANGE_UP"
    assert calls["frequency"] == 5
    assert client.closed

    candidate_files = list(
        tmp_path.glob(
            "cycle-*-movers-nasdaq-percent_change_up-run.json"
        )
    )
    assert len(candidate_files) == 1

    candidate_record = json.loads(
        candidate_files[0].read_text(encoding="utf-8")
    )
    assert candidate_record["input_mode"] == "api"
    assert candidate_record["replay_file"] is None
    assert candidate_record["request_url"] == (
        "https://example.test/marketdata/v1/movers"
    )
    assert candidate_record["http_status"] == 200
    assert candidate_record["watchlist_submit_requested"] is False


def test_cycle_cli_live_requires_encrypted_config(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    missing_path = tmp_path / "missing.ecfg"

    def unexpected_password_prompt(prompt: str) -> str:
        raise AssertionError("Password prompt was unexpected.")

    monkeypatch.setattr(
        run_watchlist_cycle.getpass,
        "getpass",
        unexpected_password_prompt,
    )

    exit_code = run_watchlist_cycle.main(
        [
            "--ecfg",
            str(missing_path),
            "--mode",
            "replace",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "encrypted configuration does not exist" in captured.err
    assert not list(tmp_path.glob("cycle-*.json"))
