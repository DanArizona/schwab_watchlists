from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

import schwab_watchlists.tos_coordinator_executor as executor_module
from mb_watchlist_coordinator.health import (
    AdapterHealthStatus,
)
from schwab_watchlists.tos_coordinator_executor import (
    LiveToSExecutor,
)
from mb_watchlist_coordinator.execution import (
    MaterializationExecutionStatus,
)
from mb_watchlist_coordinator.transactions import (
    MaterializationTransaction,
    start_transaction,
)


NOW = datetime(
    2026,
    8,
    26,
    5,
    0,
    tzinfo=timezone.utc,
)


def make_active_transaction(
    *,
    operation: str,
    operation_symbols: set[str],
    target_symbols: set[str],
) -> MaterializationTransaction:
    created = MaterializationTransaction(
        transaction_id="T-live",
        adapter_id="tos",
        target_canonical_revision=1,
        target_symbols=frozenset(target_symbols),
        operation=operation,
        operation_symbols=frozenset(operation_symbols),
        created_at=NOW,
    )

    return start_transaction(
        created,
        at=NOW,
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


# def test_materialize_is_not_enabled_yet(
#     tmp_path,
# ):
#     executor = LiveToSExecutor(
#         root=None,
#         verification_dir=tmp_path,
#         wait=30.0,
#         preflight_checker=lambda **kwargs: None,
#         control_executor=lambda **kwargs: None,
#     )

#     with pytest.raises(
#         NotImplementedError,
#         match="next integration step",
#     ):
#         executor.materialize(None)


def test_materialize_add_returns_complete_observation(
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

    submitted = {}

    def submitter(**kwargs):
        submitted.update(kwargs)

        return SimpleNamespace(
            submitted=True,
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
        verification_dir=tmp_path / "verify",
        wait=30.0,
        preflight_checker=preflight_checker,
        control_executor=control_executor,
        output_dir=tmp_path / "output",
        submitter=submitter,
    )

    transaction = make_active_transaction(
        operation="ADD",
        operation_symbols={"TEMC"},
        target_symbols={"AAPL", "NVDA", "TEMC"},
    )

    result = executor.materialize(transaction)

    assert result.status is (
        MaterializationExecutionStatus.OBSERVED
    )

    assert result.observed_state is not None
    assert result.observed_state.symbols == frozenset(
        {"AAPL", "NVDA", "TEMC"}
    )

    assert submitted["mode"] == "add"
    assert submitted["symbols"] == ["TEMC"]

    assert control_calls == [
        "suspend_exports",
        "resume_exports",
    ]


def test_materialize_replace_submits_complete_operation_set(
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

    submitted = {}

    def submitter(**kwargs):
        submitted.update(kwargs)

        return SimpleNamespace(
            submitted=True,
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

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path / "verify",
        wait=30.0,
        preflight_checker=preflight_checker,
        control_executor=lambda **kwargs: SimpleNamespace(
            successful=True,
            return_code=0,
        ),
        output_dir=tmp_path / "output",
        submitter=submitter,
    )

    transaction = make_active_transaction(
        operation="REPLACE",
        operation_symbols={"AAPL", "NVDA"},
        target_symbols={"AAPL", "NVDA"},
    )

    result = executor.materialize(transaction)

    assert result.status is (
        MaterializationExecutionStatus.OBSERVED
    )

    assert submitted["mode"] == "replace"
    assert submitted["symbols"] == [
        "AAPL",
        "NVDA",
    ]


def test_unsuccessful_submitted_mutation_is_outcome_unknown(
    tmp_path,
):
    states = [
        make_scanner_state(suspended=False),
        make_scanner_state(suspended=True),
        make_scanner_state(suspended=False),
    ]

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path / "verify",
        wait=30.0,
        preflight_checker=lambda **kwargs: states.pop(0),
        control_executor=lambda **kwargs: SimpleNamespace(
            successful=True,
            return_code=0,
        ),
        output_dir=tmp_path / "output",
        submitter=lambda **kwargs: SimpleNamespace(
            submitted=True,
            successful=False,
            return_code=1,
        ),
    )

    transaction = make_active_transaction(
        operation="ADD",
        operation_symbols={"TEMC"},
        target_symbols={"AAPL", "TEMC"},
    )

    result = executor.materialize(transaction)

    assert result.status is (
        MaterializationExecutionStatus.OUTCOME_UNKNOWN
    )


def test_export_failure_after_mutation_is_outcome_unknown(
    tmp_path,
    monkeypatch,
):
    states = [
        make_scanner_state(suspended=False),
        make_scanner_state(suspended=True),
        make_scanner_state(suspended=False),
    ]

    monkeypatch.setattr(
        executor_module,
        "run_watchlist_export",
        lambda **kwargs: (
            ("mb-scan-command", "export_wl"),
            1,
        ),
    )

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path / "verify",
        wait=30.0,
        preflight_checker=lambda **kwargs: states.pop(0),
        control_executor=lambda **kwargs: SimpleNamespace(
            successful=True,
            return_code=0,
        ),
        output_dir=tmp_path / "output",
        submitter=lambda **kwargs: SimpleNamespace(
            submitted=True,
            successful=True,
            return_code=0,
        ),
    )

    transaction = make_active_transaction(
        operation="ADD",
        operation_symbols={"TEMC"},
        target_symbols={"AAPL", "TEMC"},
    )

    result = executor.materialize(transaction)

    assert result.status is (
        MaterializationExecutionStatus.OUTCOME_UNKNOWN
    )


def test_materialize_keeps_observation_when_resume_fails(
    tmp_path,
    monkeypatch,
):
    states = [
        make_scanner_state(suspended=False),
        make_scanner_state(suspended=True),
    ]

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
        lambda path: {"AAPL", "TEMC"},
    )

    monkeypatch.setattr(
        LiveToSExecutor,
        "_resume_exports",
        lambda self: (_ for _ in ()).throw(
            RuntimeError(
                "Could not restore scheduled exports"
            )
        ),
    )

    executor = LiveToSExecutor(
        root=None,
        verification_dir=tmp_path / "verify",
        wait=30.0,
        preflight_checker=lambda **kwargs: states.pop(0),
        control_executor=lambda **kwargs: SimpleNamespace(
            successful=True,
            return_code=0,
        ),
        output_dir=tmp_path / "output",
        submitter=lambda **kwargs: SimpleNamespace(
            submitted=True,
            successful=True,
            return_code=0,
        ),
    )

    transaction = make_active_transaction(
        operation="ADD",
        operation_symbols={"TEMC"},
        target_symbols={"AAPL", "TEMC"},
    )

    result = executor.materialize(transaction)

    assert result.status is (
        MaterializationExecutionStatus.OBSERVED
    )

    assert result.observed_state is not None

    assert result.health_state is not None
    assert result.health_state.status is (
        AdapterHealthStatus.DEGRADED
    )
