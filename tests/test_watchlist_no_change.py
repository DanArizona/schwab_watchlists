from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_watchlist_cycle
from candidate_filters import FilterSettings
from candidate_model import SymbolCandidate
from schwab_movers_source import SchwabMoversBatch
from watchlist_change_detection import (
    find_latest_applied_replacement,
    find_unchanged_replacement,
)
from watchlist_cycle import (
    CYCLE_STATUS_NO_CHANGE,
    CYCLE_STATUS_PLAN_CREATED,
    run_schwab_movers_cycle,
)
from watchlist_cycle_index import (
    CYCLE_STATE_NO_CHANGE,
    discover_watchlist_cycles,
)

TIME_ZONE = timezone(timedelta(hours=-5))
NOW = datetime(2026, 8, 4, 14, 30, 0, tzinfo=TIME_ZONE)


def make_batch() -> SchwabMoversBatch:
    candidates = (
        SymbolCandidate(
            symbol="AMIX",
            source="schwab_movers",
            as_of=NOW,
            last_price=2.0,
            percent_change=10.0,
            volume=100_000_000,
        ),
        SymbolCandidate(
            symbol="QNME",
            source="schwab_movers",
            as_of=NOW,
            last_price=3.0,
            percent_change=8.0,
            volume=90_000_000,
        ),
        SymbolCandidate(
            symbol="ADGM",
            source="schwab_movers",
            as_of=NOW,
            last_price=4.0,
            percent_change=6.0,
            volume=80_000_000,
        ),
    )
    return SchwabMoversBatch(
        market="NASDAQ",
        sort_name="PERCENT_CHANGE_UP",
        frequency=5,
        requested_at=NOW,
        request_url="https://example.test/movers",
        status_code=200,
        records=(),
        candidates=candidates,
        raw_data={"screeners": []},
    )


def write_application(
    output_dir: Path,
    *,
    cycle_id: str,
    created_at: str,
    symbols: list[str],
    return_code: int = 0,
    mode: str = "replace",
) -> Path:
    plan_path = output_dir / f"{cycle_id}-wl-{mode}-run.json"
    plan_path.write_text("{}", encoding="utf-8")
    run_path = output_dir / f"{created_at[:19].replace(':', '-')}-wl-{mode}-run.json"
    run_path.write_text(
        json.dumps(
            {
                "created_at": created_at,
                "record_origin": "plan_application",
                "cycle_id": cycle_id,
                "mode": mode,
                "submitted": True,
                "symbols": symbols,
                "return_code": return_code,
                "source_plan_file": str(plan_path.resolve()),
            }
        ),
        encoding="utf-8",
    )
    return run_path


def test_latest_successful_replacement_ignores_failed_record(
    tmp_path: Path,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-140000-old00001",
        created_at="2026-08-04T14:00:00-05:00",
        symbols=["OLD"],
    )
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141000-failed01",
        created_at="2026-08-04T14:10:00-05:00",
        symbols=["FAILED"],
        return_code=1,
    )

    latest = find_latest_applied_replacement(tmp_path)

    assert latest is not None
    assert latest.cycle_id == "cycle-20260804-140000-old00001"
    assert latest.symbols == ("OLD",)


def test_replacement_match_requires_exact_symbol_order(
    tmp_path: Path,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["AMIX", "QNME", "ADGM"],
    )

    assert find_unchanged_replacement(
        output_dir=tmp_path,
        mode="replace",
        symbols=("AMIX", "QNME", "ADGM"),
    ) is not None
    assert find_unchanged_replacement(
        output_dir=tmp_path,
        mode="replace",
        symbols=("QNME", "AMIX", "ADGM"),
    ) is None


def test_add_mode_is_never_suppressed(
    tmp_path: Path,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["AMIX", "QNME", "ADGM"],
    )

    assert find_unchanged_replacement(
        output_dir=tmp_path,
        mode="add",
        symbols=("AMIX", "QNME", "ADGM"),
    ) is None


def test_cycle_records_no_change_without_application(
    tmp_path: Path,
) -> None:
    previous_run = write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["AMIX", "QNME", "ADGM"],
    )

    result = run_schwab_movers_cycle(
        batch=make_batch(),
        filter_settings=FilterSettings(max_results=3),
        mode="replace",
        output_dir=tmp_path,
        input_mode="api",
        submit_requested=True,
        suppress_unchanged=True,
        cycle_id="cycle-20260804-143000-new00001",
        started_at=NOW,
        completed_at=NOW,
    )

    assert result.status == CYCLE_STATUS_NO_CHANGE
    assert result.no_change_match is not None
    assert result.no_change_match.application_run_path == previous_run.resolve()
    assert result.watchlist_plan is not None

    record = json.loads(
        result.cycle_record_path.read_text(encoding="utf-8")
    )
    assert record["status"] == CYCLE_STATUS_NO_CHANGE
    assert record["no_change_against_cycle_id"] == (
        "cycle-20260804-141725-1f75f834"
    )
    assert record["no_change_application_file"] == str(
        previous_run.resolve()
    )

    cycle = discover_watchlist_cycles(tmp_path).cycles[0]
    assert cycle.state == CYCLE_STATE_NO_CHANGE
    assert not cycle.pending
    assert cycle.no_change_against_cycle_id == (
        "cycle-20260804-141725-1f75f834"
    )


def test_changed_replacement_remains_plan_created(
    tmp_path: Path,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["OTHER"],
    )

    result = run_schwab_movers_cycle(
        batch=make_batch(),
        filter_settings=FilterSettings(max_results=3),
        mode="replace",
        output_dir=tmp_path,
        input_mode="api",
        submit_requested=True,
        suppress_unchanged=True,
        cycle_id="cycle-20260804-143000-new00002",
        started_at=NOW,
        completed_at=NOW,
    )

    assert result.status == CYCLE_STATUS_PLAN_CREATED
    assert result.no_change_match is None


def test_force_submit_requires_submit(
    capsys,
) -> None:
    exit_code = run_watchlist_cycle.main(
        [
            "--mode",
            "replace",
            "--force-submit",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--force-submit requires --submit" in captured.err


def test_cli_skips_unchanged_live_replacement(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["AMIX", "QNME", "ADGM"],
    )
    ecfg_path = tmp_path / "secure_schwabdev.ecfg"
    ecfg_path.write_text("test", encoding="utf-8")

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        run_watchlist_cycle.getpass,
        "getpass",
        lambda prompt: "test-password",
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "make_secure_schwab_client",
        lambda *args, **kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "fetch_schwab_movers",
        lambda *args, **kwargs: make_batch(),
    )

    def unexpected_application(**kwargs):
        raise AssertionError("Application was unexpected.")

    monkeypatch.setattr(
        run_watchlist_cycle,
        "apply_frozen_cycle_plan",
        unexpected_application,
    )

    exit_code = run_watchlist_cycle.main(
        [
            "--ecfg",
            str(ecfg_path),
            "--mode",
            "replace",
            "--limit",
            "3",
            "--submit",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "Status           : no_change" in captured.out
    assert "No Watchlist change detected." in captured.out
    assert "No Watchlist command was published." in captured.out


def test_suppression_can_be_bypassed_for_force_submit(
    tmp_path: Path,
) -> None:
    write_application(
        tmp_path,
        cycle_id="cycle-20260804-141725-1f75f834",
        created_at="2026-08-04T14:17:25-05:00",
        symbols=["AMIX", "QNME", "ADGM"],
    )

    result = run_schwab_movers_cycle(
        batch=make_batch(),
        filter_settings=FilterSettings(max_results=3),
        mode="replace",
        output_dir=tmp_path,
        input_mode="api",
        submit_requested=True,
        suppress_unchanged=False,
        cycle_id="cycle-20260804-143000-force001",
        started_at=NOW,
        completed_at=NOW,
    )

    assert result.status == CYCLE_STATUS_PLAN_CREATED
    assert result.no_change_match is None
