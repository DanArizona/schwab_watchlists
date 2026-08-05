from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import run_watchlist_controller
from watchlist_cycle_schedule import (
    SCHEDULE_PHASE_CLOSED,
    SCHEDULE_PHASE_OPENING,
    SCHEDULE_STATUS_DUE,
    SCHEDULE_STATUS_OUTSIDE_SESSION,
    WatchlistCycleScheduleDecision,
)


NY = ZoneInfo("America/New_York")


def decision(
    *,
    evaluated_at: datetime,
    status: str,
    due: bool,
    phase: str,
) -> WatchlistCycleScheduleDecision:
    session_start = datetime.combine(
        evaluated_at.date(),
        time(9, 30),
        tzinfo=NY,
    )
    session_end = datetime.combine(
        evaluated_at.date(),
        time(16, 0),
        tzinfo=NY,
    )

    return WatchlistCycleScheduleDecision(
        status=status,
        due=due,
        reason="test decision",
        evaluated_at=evaluated_at,
        session_start=session_start,
        session_end=session_end,
        phase=phase,
        interval=None,
    )


def parser_args(
    tmp_path: Path,
    *extra: str,
):
    ecfg = tmp_path / "secure.ecfg"
    ecfg.write_text("test", encoding="utf-8")

    return (
        run_watchlist_controller
        .build_parser()
        .parse_args(
            [
                "--mode",
                "replace",
                "--ecfg",
                str(ecfg),
                "--output-dir",
                str(tmp_path / "output"),
                *extra,
            ]
        )
    )


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def fake_lock_factory(
    lock_path: Path,
    *,
    command,
):
    return nullcontext()


def test_after_session_exits_before_password(
    tmp_path: Path,
) -> None:
    args = parser_args(tmp_path)

    after_close = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            16,
            1,
            tzinfo=NY,
        ),
        status=SCHEDULE_STATUS_OUTSIDE_SESSION,
        due=False,
        phase=SCHEDULE_PHASE_CLOSED,
    )

    def unexpected_password(prompt: str) -> str:
        raise AssertionError(
            "Password prompt was unexpected."
        )

    def unexpected_client(*args, **kwargs):
        raise AssertionError(
            "Client creation was unexpected."
        )

    exit_code = (
        run_watchlist_controller.run_controller(
            args,
            password_reader=unexpected_password,
            client_factory=unexpected_client,
            schedule_evaluator=(
                lambda output_dir: after_close
            ),
            lock_factory=fake_lock_factory,
        )
    )

    assert exit_code == 0


def test_two_due_cycles_share_one_client_and_password(
    tmp_path: Path,
) -> None:
    args = parser_args(
        tmp_path,
        "--max-cycles",
        "2",
    )

    due = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            9,
            30,
            tzinfo=NY,
        ),
        status=SCHEDULE_STATUS_DUE,
        due=True,
        phase=SCHEDULE_PHASE_OPENING,
    )

    password_calls: list[str] = []
    factory_calls: list[tuple[Path, str]] = []
    cycle_clients: list[FakeClient] = []
    client = FakeClient()

    def password_reader(prompt: str) -> str:
        password_calls.append(prompt)
        return "secret"

    def client_factory(
        ecfg_path: Path,
        password: str,
        *,
        timeout: int,
    ) -> FakeClient:
        factory_calls.append(
            (ecfg_path, password)
        )
        return client

    def cycle_runner(
        *,
        client,
        args,
        filter_settings,
    ) -> int:
        cycle_clients.append(client)
        return 0

    exit_code = (
        run_watchlist_controller.run_controller(
            args,
            password_reader=password_reader,
            client_factory=client_factory,
            schedule_evaluator=(
                lambda output_dir: due
            ),
            sleep_fn=lambda seconds: None,
            due_cycle_runner=cycle_runner,
            lock_factory=fake_lock_factory,
        )
    )

    assert exit_code == 0
    assert len(password_calls) == 1
    assert len(factory_calls) == 1
    assert cycle_clients == [
        client,
        client,
    ]
    assert client.closed


def test_controller_waits_before_session(
    tmp_path: Path,
) -> None:
    args = parser_args(
        tmp_path,
        "--max-cycles",
        "1",
        "--poll-seconds",
        "7",
    )

    before_open = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            9,
            0,
            tzinfo=NY,
        ),
        status=SCHEDULE_STATUS_OUTSIDE_SESSION,
        due=False,
        phase=SCHEDULE_PHASE_CLOSED,
    )

    due = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            9,
            30,
            tzinfo=NY,
        ),
        status=SCHEDULE_STATUS_DUE,
        due=True,
        phase=SCHEDULE_PHASE_OPENING,
    )

    decisions = iter(
        (before_open, due)
    )
    sleeps: list[float] = []
    client = FakeClient()

    exit_code = (
        run_watchlist_controller.run_controller(
            args,
            password_reader=lambda prompt: "secret",
            client_factory=(
                lambda *args, **kwargs: client
            ),
            schedule_evaluator=(
                lambda output_dir: next(decisions)
            ),
            sleep_fn=sleeps.append,
            due_cycle_runner=(
                lambda **kwargs: 0
            ),
            lock_factory=fake_lock_factory,
        )
    )

    assert exit_code == 0
    assert sleeps == [7.0]
    assert client.closed


def test_consecutive_exceptions_stop_controller(
    tmp_path: Path,
) -> None:
    args = parser_args(
        tmp_path,
        "--max-consecutive-failures",
        "2",
        "--failure-backoff-seconds",
        "3",
    )

    due = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            9,
            30,
            tzinfo=NY,
        ),
        status=SCHEDULE_STATUS_DUE,
        due=True,
        phase=SCHEDULE_PHASE_OPENING,
    )

    sleeps: list[float] = []
    client = FakeClient()

    def failing_cycle(**kwargs) -> int:
        raise RuntimeError(
            "simulated API failure"
        )

    exit_code = (
        run_watchlist_controller.run_controller(
            args,
            password_reader=lambda prompt: "secret",
            client_factory=(
                lambda *args, **kwargs: client
            ),
            schedule_evaluator=(
                lambda output_dir: due
            ),
            sleep_fn=sleeps.append,
            due_cycle_runner=failing_cycle,
            lock_factory=fake_lock_factory,
        )
    )

    assert exit_code == 1
    assert sleeps == [3.0, 15.0]
    assert client.closed


def test_force_submit_requires_submit(
    tmp_path: Path,
) -> None:
    args = parser_args(
        tmp_path,
        "--force-submit",
    )

    with pytest.raises(
        ValueError,
        match="requires --submit",
    ):
        run_watchlist_controller.run_controller(
            args,
            lock_factory=fake_lock_factory,
        )


def test_run_due_cycle_dry_run_never_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = parser_args(tmp_path)
    filter_settings = (
        run_watchlist_controller
        ._build_filter_settings(args)
    )

    fake_batch = SimpleNamespace(
        request_url="https://example.test",
        status_code=200,
    )
    fake_result = SimpleNamespace(
        cycle_id="cycle-test",
        strategy_name="movers_demo_v1",
        watchlist_mode="replace",
        status="plan_created",
        pipeline_result=SimpleNamespace(
            input_count=2,
            accepted_count=2,
            rejected_count=0,
            accepted_symbols=(
                "AAA",
                "BBB",
            ),
        ),
        candidate_outputs=SimpleNamespace(
            run_json=tmp_path
            / "candidate.json",
        ),
        cycle_record_path=(
            tmp_path / "cycle.json"
        ),
        watchlist_plan=SimpleNamespace(
            run_record_path=(
                tmp_path / "plan.json"
            ),
        ),
        no_change_match=None,
    )

    def unexpected_apply(**kwargs):
        raise AssertionError(
            "Plan application was unexpected."
        )

    exit_code = (
        run_watchlist_controller.run_due_cycle(
            client=object(),
            args=args,
            filter_settings=filter_settings,
            fetcher=(
                lambda *args, **kwargs: fake_batch
            ),
            cycle_runner=(
                lambda **kwargs: fake_result
            ),
            plan_applier=unexpected_apply,
        )
    )

    assert exit_code == 0


def test_keyboard_interrupt_closes_client(
    tmp_path: Path,
) -> None:
    args = parser_args(tmp_path)

    not_due = decision(
        evaluated_at=datetime(
            2026,
            8,
            5,
            10,
            0,
            tzinfo=NY,
        ),
        status="NOT_DUE",
        due=False,
        phase="early",
    )

    client = FakeClient()

    def interrupting_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    exit_code = (
        run_watchlist_controller.run_controller(
            args,
            password_reader=lambda prompt: "secret",
            client_factory=(
                lambda *args, **kwargs: client
            ),
            schedule_evaluator=(
                lambda output_dir: not_due
            ),
            sleep_fn=interrupting_sleep,
            lock_factory=fake_lock_factory,
        )
    )

    assert exit_code == 0
    assert client.closed


def test_invalid_poll_interval_is_rejected(
    tmp_path: Path,
) -> None:
    args = parser_args(
        tmp_path,
        "--poll-seconds",
        "0",
    )

    with pytest.raises(
        ValueError,
        match="must be positive",
    ):
        run_watchlist_controller.run_controller(
            args,
            lock_factory=fake_lock_factory,
        )
