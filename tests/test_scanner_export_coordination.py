from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import run_watchlist_cycle
from scanner_export_coordination import (
    ScannerControlResult,
    apply_watchlist_plan_with_export_suspension,
)
from scanner_preflight import ScannerPreflightResult


def preflight_result(
    tmp_path: Path,
    *,
    suspended: bool,
    ready: bool = True,
) -> ScannerPreflightResult:
    return ScannerPreflightResult(
        root=tmp_path,
        ready=ready,
        status="HEALTHY" if ready else "UNKNOWN",
        detail=(
            "synthetic ready"
            if ready
            else "synthetic not ready"
        ),
        loop_state=(
            "exports_suspended"
            if suspended
            else "idle"
        ),
        running=True,
        paused=False,
        age_seconds=1.0,
        exports_suspended=suspended,
    )


def test_apply_frozen_cycle_plan_forwards_preflight_checker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SimpleNamespace(
        cycle_id="cycle-test",
        mode="replace",
        symbols=("AAA", "BBB"),
        source_path=tmp_path / "plan.json",
        created_at="2026-08-07T10:00:00-04:00",
    )
    monkeypatch.setattr(
        run_watchlist_cycle,
        "load_watchlist_plan",
        lambda path: plan,
    )

    calls = {}

    def checker(**kwargs):
        return object()

    def submitter(**kwargs):
        calls.update(kwargs)
        return object()

    result = (
        run_watchlist_cycle
        .apply_frozen_cycle_plan(
            plan_path=plan.source_path,
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            submitter=submitter,
            preflight_checker=checker,
        )
    )

    assert result is not None
    assert calls["preflight_checker"] is checker
    assert calls["symbols"] == ("AAA", "BBB")


def test_successful_coordinated_apply_resumes_exports(
    tmp_path: Path,
) -> None:
    gate = {"suspended": False}
    events: list[str] = []

    def checker(
        *,
        root: Path | None,
        allow_exports_suspended: bool = False,
    ) -> ScannerPreflightResult:
        events.append(
            "preflight:suspended"
            if allow_exports_suspended
            else "preflight:active"
        )
        suspended = gate["suspended"]
        ready = (
            suspended
            if allow_exports_suspended
            else not suspended
        )
        return preflight_result(
            tmp_path,
            suspended=suspended,
            ready=ready,
        )

    def control_executor(
        *,
        action: str,
        root: Path | None,
        wait: float,
    ) -> ScannerControlResult:
        events.append(action)
        gate["suspended"] = (
            action == "suspend_exports"
        )
        return ScannerControlResult(
            action=action,
            command=("mb-scan-command", action),
            return_code=0,
        )

    def plan_applier(**kwargs):
        events.append("apply")
        result = kwargs["preflight_checker"](
            root=tmp_path
        )
        assert result.ready
        assert result.exports_suspended
        return SimpleNamespace(successful=True)

    coordinated = (
        apply_watchlist_plan_with_export_suspension(
            plan_applier=plan_applier,
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            control_executor=control_executor,
            preflight_checker=checker,
        )
    )

    assert coordinated.application.successful
    assert coordinated.suspend_command.successful
    assert coordinated.resume_command.successful
    assert coordinated.resumed_preflight.ready
    assert gate["suspended"] is False
    assert events == [
        "preflight:active",
        "suspend_exports",
        "preflight:suspended",
        "apply",
        "preflight:suspended",
        "resume_exports",
        "preflight:active",
    ]


def test_plan_failure_still_resumes_exports(
    tmp_path: Path,
) -> None:
    gate = {"suspended": False}
    controls: list[str] = []

    def checker(
        *,
        root: Path | None,
        allow_exports_suspended: bool = False,
    ) -> ScannerPreflightResult:
        suspended = gate["suspended"]
        return preflight_result(
            tmp_path,
            suspended=suspended,
            ready=(
                suspended
                if allow_exports_suspended
                else not suspended
            ),
        )

    def control_executor(
        *,
        action: str,
        root: Path | None,
        wait: float,
    ) -> ScannerControlResult:
        controls.append(action)
        gate["suspended"] = (
            action == "suspend_exports"
        )
        return ScannerControlResult(
            action=action,
            command=("mb-scan-command", action),
            return_code=0,
        )

    def failing_plan(**kwargs):
        raise RuntimeError(
            "simulated Watchlist replacement failure"
        )

    with pytest.raises(
        RuntimeError,
        match="simulated Watchlist replacement failure",
    ):
        apply_watchlist_plan_with_export_suspension(
            plan_applier=failing_plan,
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            control_executor=control_executor,
            preflight_checker=checker,
        )

    assert controls == [
        "suspend_exports",
        "resume_exports",
    ]
    assert gate["suspended"] is False


def test_failed_suspend_command_still_attempts_resume(
    tmp_path: Path,
) -> None:
    controls: list[str] = []

    def checker(
        *,
        root: Path | None,
        allow_exports_suspended: bool = False,
    ) -> ScannerPreflightResult:
        return preflight_result(
            tmp_path,
            suspended=False,
            ready=not allow_exports_suspended,
        )

    def control_executor(
        *,
        action: str,
        root: Path | None,
        wait: float,
    ) -> ScannerControlResult:
        controls.append(action)
        return ScannerControlResult(
            action=action,
            command=("mb-scan-command", action),
            return_code=(
                3
                if action == "suspend_exports"
                else 0
            ),
        )

    with pytest.raises(
        RuntimeError,
        match="suspend_exports was not reported",
    ):
        apply_watchlist_plan_with_export_suspension(
            plan_applier=lambda **kwargs: None,
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            control_executor=control_executor,
            preflight_checker=checker,
        )

    assert controls == [
        "suspend_exports",
        "resume_exports",
    ]


def test_initial_preflight_failure_publishes_no_control_command(
    tmp_path: Path,
) -> None:
    controls: list[str] = []

    def control_executor(**kwargs):
        controls.append(kwargs["action"])
        raise AssertionError(
            "Control command should not run."
        )

    with pytest.raises(
        RuntimeError,
        match="preflight before export suspension failed",
    ):
        apply_watchlist_plan_with_export_suspension(
            plan_applier=lambda **kwargs: None,
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            control_executor=control_executor,
            preflight_checker=(
                lambda **kwargs: preflight_result(
                    tmp_path,
                    suspended=False,
                    ready=False,
                )
            ),
        )

    assert controls == []


def test_resume_failure_is_reported_as_recovery_failure(
    tmp_path: Path,
) -> None:
    gate = {"suspended": False}

    def checker(
        *,
        root: Path | None,
        allow_exports_suspended: bool = False,
    ) -> ScannerPreflightResult:
        suspended = gate["suspended"]
        return preflight_result(
            tmp_path,
            suspended=suspended,
            ready=(
                suspended
                if allow_exports_suspended
                else not suspended
            ),
        )

    def control_executor(
        *,
        action: str,
        root: Path | None,
        wait: float,
    ) -> ScannerControlResult:
        if action == "suspend_exports":
            gate["suspended"] = True
            return ScannerControlResult(
                action=action,
                command=("mb-scan-command", action),
                return_code=0,
            )

        return ScannerControlResult(
            action=action,
            command=("mb-scan-command", action),
            return_code=1,
        )

    with pytest.raises(
        RuntimeError,
        match="Scanner exports may remain suspended",
    ):
        apply_watchlist_plan_with_export_suspension(
            plan_applier=(
                lambda **kwargs: SimpleNamespace(
                    successful=True
                )
            ),
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=30.0,
            control_executor=control_executor,
            preflight_checker=checker,
        )


def test_nonpositive_wait_is_rejected_before_preflight(
    tmp_path: Path,
) -> None:
    def unexpected_preflight(**kwargs):
        raise AssertionError(
            "Preflight should not run."
        )

    with pytest.raises(
        ValueError,
        match="positive scanner wait",
    ):
        apply_watchlist_plan_with_export_suspension(
            plan_applier=lambda **kwargs: None,
            plan_path=tmp_path / "plan.json",
            expected_cycle_id="cycle-test",
            output_dir=tmp_path,
            root=tmp_path,
            wait=0.0,
            preflight_checker=unexpected_preflight,
        )
