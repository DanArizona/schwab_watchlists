from pathlib import Path
from types import SimpleNamespace

import pytest

import schwab_watchlists.tos_coordinator_executor as executor_module
from mb_watchlist_coordinator.health import (
    AdapterHealthStatus,
)
from schwab_watchlists.tos_coordinator_executor import (
    LiveToSExecutor,
)


def make_scanner_state(
    *,
    suspended: bool,
):
    return SimpleNamespace(
        ready=True,
        status="HEALTHY",
        loop_state=(
            "exports_suspended"
            if suspended
            else "idle"
        ),
        running=True,
        paused=False,
        exports_suspended=suspended,
        detail="ready",
    )


def test_observe_returns_complete_watchlist(
    tmp_path,
    monkeypatch,
):
    states = [
        make_scanner_state(suspended=False),
        make_scanner_state(suspended=True),
        make_scanner_state(suspended=False),
    ]

    def preflight_checker(**kwargs):
        return states.pop(0)

    control_calls = []

    def control_executor(**kwargs):
        control_calls.append(kwargs["action"])

        return SimpleNamespace(
            successful=True,
            return_code=0,
        )

    monkeypatch.setattr(
        executor_module,
        "run_watchlist_export",
        lambda **kwargs: (
            ("mb-scan-command", "export_wl"),
            0,
        ),
    )

    monkeypatch.setattr(
        executor_module,
        "wait_for_file",
        lambda path, timeout: True,
    )

    monkeypatch.setattr(
        executor_module,
        "read_watchlist_symbols",
        lambda path: {
            "AAPL",
            "NVDA",
            "TEMC",
        },
    )

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path,
        wait=30.0,
        preflight_checker=preflight_checker,
        control_executor=control_executor,
    )

    result = executor.observe()

    assert result.observed_state.adapter_id == "tos"
    assert result.observed_state.symbols == frozenset(
        {"AAPL", "NVDA", "TEMC"}
    )

    assert result.health_state is not None
    assert result.health_state.status is (
        AdapterHealthStatus.HEALTHY
    )

    assert control_calls == [
        "suspend_exports",
        "resume_exports",
    ]


def test_observe_preserves_observation_if_resume_fails(
    tmp_path,
    monkeypatch,
):
    states = [
        make_scanner_state(suspended=False),
        make_scanner_state(suspended=True),
    ]

    def preflight_checker(**kwargs):
        return states.pop(0)

    def control_executor(**kwargs):
        if kwargs["action"] == "resume_exports":
            return SimpleNamespace(
                successful=False,
                return_code=1,
            )

        return SimpleNamespace(
            successful=True,
            return_code=0,
        )

    monkeypatch.setattr(
        executor_module,
        "run_watchlist_export",
        lambda **kwargs: (
            ("mb-scan-command", "export_wl"),
            0,
        ),
    )

    monkeypatch.setattr(
        executor_module,
        "wait_for_file",
        lambda path, timeout: True,
    )

    monkeypatch.setattr(
        executor_module,
        "read_watchlist_symbols",
        lambda path: {"AAPL", "NVDA"},
    )

    monkeypatch.setattr(
        executor_module,
        "resume_exports_with_retry",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "Could not restore scheduled exports"
            )
        ),
    )

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path,
        wait=30.0,
        preflight_checker=preflight_checker,
        control_executor=control_executor,
    )

    result = executor.observe()

    assert result.observed_state.symbols == frozenset(
        {"AAPL", "NVDA"}
    )

    assert result.health_state is not None
    assert result.health_state.status is (
        AdapterHealthStatus.DEGRADED
    )

    assert "restore scheduled exports" in (
        result.health_state.reason
    )


def test_materialize_is_not_enabled_yet(
    tmp_path,
):
    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path,
        wait=30.0,
        preflight_checker=lambda **kwargs: None,
        control_executor=lambda **kwargs: None,
    )

    with pytest.raises(
        NotImplementedError,
        match="next integration step",
    ):
        executor.materialize(None)
