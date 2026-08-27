from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

import run_ludp_coordinator as module

from mb_watchlist_coordinator.models import (
    IntentType,
)


EASTERN = ZoneInfo("America/New_York")


class FakeMonitor:
    def __init__(self, pending):
        self.pending = tuple(pending)
        self.seen_symbols = set()
        self.mark_calls = []

    def pending_symbols(
        self,
        records,
        *,
        session_date,
    ):
        return tuple(
            symbol
            for symbol in self.pending
            if symbol not in self.seen_symbols
        )

    def mark_seen(
        self,
        symbols,
        *,
        session_date,
    ):
        symbols = tuple(symbols)

        self.mark_calls.append(
            symbols
        )

        self.seen_symbols.update(
            symbols
        )


class FakeCoordinator:
    def __init__(self):
        self.intents = []
        self.current_canonical = (
            SimpleNamespace(
                revision=1,
            )
        )

    def accept_intent(
        self,
        intent,
        *,
        at,
    ):
        self.intents.append(
            intent
        )

        self.current_canonical = (
            SimpleNamespace(
                revision=(
                    self.current_canonical.revision
                    + 1
                ),
            )
        )


def test_determine_startup_mode_accepts_baseline():
    args = SimpleNamespace(
        baseline=Path("baseline.csv"),
        ov_watchlist=None,
        ov_limit=None,
    )

    assert (
        module.determine_startup_mode(
            args
        )
        == "baseline"
    )


def test_determine_startup_mode_accepts_ov():
    args = SimpleNamespace(
        baseline=None,
        ov_watchlist=Path(
            "ov.csv"
        ),
        ov_limit=25,
    )

    assert (
        module.determine_startup_mode(
            args
        )
        == "ov"
    )


def test_determine_startup_mode_rejects_both_sources():
    args = SimpleNamespace(
        baseline=Path(
            "baseline.csv"
        ),
        ov_watchlist=Path(
            "ov.csv"
        ),
        ov_limit=25,
    )

    with pytest.raises(
        ValueError,
        match="exactly one",
    ):
        module.determine_startup_mode(
            args
        )


def test_determine_startup_mode_requires_positive_ov_limit():
    args = SimpleNamespace(
        baseline=None,
        ov_watchlist=Path(
            "ov.csv"
        ),
        ov_limit=0,
    )

    with pytest.raises(
        ValueError,
        match="ov-limit",
    ):
        module.determine_startup_mode(
            args
        )


def test_first_successful_poll_establishes_baseline_without_intent(
    monkeypatch,
):
    monitor = FakeMonitor(
        ["OLD1", "OLD2"]
    )

    coordinator = FakeCoordinator()

    state = module.LudpPollingState(
        session_date=datetime(
            2026,
            8,
            27,
            tzinfo=EASTERN,
        ).date()
    )

    reconcile_calls = []

    monkeypatch.setattr(
        module,
        "reconcile_tos_until_stable",
        lambda *args, **kwargs: (
            reconcile_calls.append("called")
        ),
    )

    handled = module.process_ludp_poll(
        coordinator=coordinator,
        executor=SimpleNamespace(),
        monitor=monitor,
        state=state,
        records=(),
        poll_time=datetime(
            2026,
            8,
            27,
            9,
            35,
            tzinfo=EASTERN,
        ),
    )

    assert handled == ()
    assert state.baseline_established is True

    assert coordinator.intents == []

    assert monitor.seen_symbols == {
        "OLD1",
        "OLD2",
    }

    assert reconcile_calls == []


def test_new_ludp_symbols_create_intent_reconcile_then_mark_seen(
    monkeypatch,
):
    monitor = FakeMonitor(
        ["TEMC"]
    )

    coordinator = FakeCoordinator()

    state = module.LudpPollingState(
        session_date=datetime(
            2026,
            8,
            27,
            tzinfo=EASTERN,
        ).date(),
        baseline_established=True,
    )

    events = []

    def fake_reconcile(
        *args,
        **kwargs,
    ):
        events.append("reconcile")

        return (
            SimpleNamespace(),
        )

    monkeypatch.setattr(
        module,
        "reconcile_tos_until_stable",
        fake_reconcile,
    )

    handled = module.process_ludp_poll(
        coordinator=coordinator,
        executor=SimpleNamespace(),
        monitor=monitor,
        state=state,
        records=(),
        poll_time=datetime(
            2026,
            8,
            27,
            10,
            15,
            tzinfo=EASTERN,
        ),
    )

    assert handled == (
        "TEMC",
    )

    assert len(
        coordinator.intents
    ) == 1

    intent = coordinator.intents[0]

    assert (
        intent.intent_type
        is IntentType.ENSURE_PRESENT
    )

    assert intent.symbols == frozenset(
        {"TEMC"}
    )

    assert events == [
        "reconcile",
    ]

    assert monitor.seen_symbols == {
        "TEMC"
    }


def test_failed_reconciliation_leaves_symbol_pending(
    monkeypatch,
):
    monitor = FakeMonitor(
        ["TEMC"]
    )

    coordinator = FakeCoordinator()

    state = module.LudpPollingState(
        session_date=datetime(
            2026,
            8,
            27,
            tzinfo=EASTERN,
        ).date(),
        baseline_established=True,
    )

    monkeypatch.setattr(
        module,
        "reconcile_tos_until_stable",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                RuntimeError(
                    "simulated ToS failure"
                )
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated ToS failure",
    ):
        module.process_ludp_poll(
            coordinator=coordinator,
            executor=SimpleNamespace(),
            monitor=monitor,
            state=state,
            records=(),
            poll_time=datetime(
                2026,
                8,
                27,
                10,
                15,
                tzinfo=EASTERN,
            ),
        )

    assert monitor.seen_symbols == set()

    assert (
        state.accepted_ludp_symbols
        == {"TEMC"}
    )

    assert len(
        coordinator.intents
    ) == 1


def test_retry_after_failure_does_not_create_duplicate_intent(
    monkeypatch,
):
    monitor = FakeMonitor(
        ["TEMC"]
    )

    coordinator = FakeCoordinator()

    state = module.LudpPollingState(
        session_date=datetime(
            2026,
            8,
            27,
            tzinfo=EASTERN,
        ).date(),
        baseline_established=True,
        accepted_ludp_symbols={
            "TEMC",
        },
    )

    reconcile_calls = []

    monkeypatch.setattr(
        module,
        "reconcile_tos_until_stable",
        lambda *args, **kwargs: (
            reconcile_calls.append(
                "reconcile"
            )
            or (
                SimpleNamespace(),
            )
        ),
    )

    handled = module.process_ludp_poll(
        coordinator=coordinator,
        executor=SimpleNamespace(),
        monitor=monitor,
        state=state,
        records=(),
        poll_time=datetime(
            2026,
            8,
            27,
            10,
            16,
            tzinfo=EASTERN,
        ),
    )

    assert handled == (
        "TEMC",
    )

    assert coordinator.intents == []

    assert reconcile_calls == [
        "reconcile",
    ]

    assert monitor.seen_symbols == {
        "TEMC"
    }
