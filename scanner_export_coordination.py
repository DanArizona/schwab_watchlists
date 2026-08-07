"""Coordinate live Watchlist replacement with scanner export suspension."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scanner_preflight import (
    ScannerPreflightResult,
    check_scanner_ready,
)


EXPORT_CONTROL_COMMANDS = frozenset(
    {
        "suspend_exports",
        "resume_exports",
    }
)


@dataclass(frozen=True, slots=True)
class ScannerControlResult:
    """Result of one scanner export-control command."""

    action: str
    command: tuple[str, ...]
    return_code: int

    @property
    def successful(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True, slots=True)
class CoordinatedWatchlistApplication:
    """Successful completion of one coordinated Watchlist application."""

    application: Any
    preflight_before: ScannerPreflightResult
    suspended_preflight: ScannerPreflightResult
    suspend_command: ScannerControlResult
    resume_command: ScannerControlResult
    resumed_preflight: ScannerPreflightResult


def build_scanner_control_command(
    *,
    action: str,
    root: Path | None,
    wait: float,
    executable: str = "mb-scan-command",
) -> tuple[str, ...]:
    """Build an mb-scan-command export-control command."""

    if action not in EXPORT_CONTROL_COMMANDS:
        raise ValueError(
            f"Unsupported scanner export-control action: {action}"
        )
    if wait <= 0:
        raise ValueError(
            "Coordinated Watchlist submission requires "
            "a positive scanner wait."
        )

    command = [
        executable,
        action,
    ]

    if root is not None:
        resolved_root = root.expanduser().resolve()
        command.extend(
            [
                "--root",
                str(resolved_root),
            ]
        )

    command.extend(
        [
            "--wait",
            str(wait),
        ]
    )

    return tuple(command)


def run_scanner_control_command(
    *,
    action: str,
    root: Path | None,
    wait: float,
    executable_finder: Callable[
        [str],
        str | None,
    ] = shutil.which,
    runner: Callable[
        ...,
        subprocess.CompletedProcess[str],
    ] = subprocess.run,
) -> ScannerControlResult:
    """Publish one scanner export-control command and wait for its result."""

    executable = executable_finder(
        "mb-scan-command"
    )

    if executable is None:
        raise RuntimeError(
            "mb-scan-command was not found on PATH."
        )

    command = build_scanner_control_command(
        action=action,
        root=root,
        wait=wait,
        executable=executable,
    )

    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Could not run mb-scan-command: {exc}"
        ) from exc

    return ScannerControlResult(
        action=action,
        command=command,
        return_code=completed.returncode,
    )


def apply_watchlist_plan_with_export_suspension(
    *,
    plan_applier: Callable[..., Any],
    plan_path: Path,
    expected_cycle_id: str,
    output_dir: Path,
    root: Path | None,
    wait: float,
    control_executor: Callable[
        ...,
        ScannerControlResult,
    ] = run_scanner_control_command,
    preflight_checker: Callable[
        ...,
        ScannerPreflightResult,
    ] = check_scanner_ready,
) -> CoordinatedWatchlistApplication:
    """
    Suspend scheduled exports, apply one frozen Watchlist plan, and resume.

    Once suspend_exports has returned, resume_exports is attempted in a
    finally block even when suspension verification or plan application fails.
    """

    if wait <= 0:
        raise ValueError(
            "Coordinated Watchlist submission requires "
            "a positive scanner wait."
        )

    preflight_before = preflight_checker(
        root=root,
    )

    if not preflight_before.ready:
        raise RuntimeError(
            "Scanner preflight before export suspension failed: "
            f"{preflight_before.detail}"
        )

    suspend_command = control_executor(
        action="suspend_exports",
        root=root,
        wait=wait,
    )

    resume_command: ScannerControlResult | None = None
    resumed_preflight: ScannerPreflightResult | None = None

    try:
        if not suspend_command.successful:
            raise RuntimeError(
                "suspend_exports was not reported as successfully "
                f"processed; exit code={suspend_command.return_code}."
            )

        suspended_preflight = preflight_checker(
            root=root,
            allow_exports_suspended=True,
        )

        if not suspended_preflight.ready:
            raise RuntimeError(
                "Scanner did not enter the expected suspended-export "
                f"state: {suspended_preflight.detail}"
            )

        def suspended_plan_preflight(
            *,
            root: Path | None,
        ) -> ScannerPreflightResult:
            return preflight_checker(
                root=root,
                allow_exports_suspended=True,
            )

        application = plan_applier(
            plan_path=plan_path,
            expected_cycle_id=expected_cycle_id,
            output_dir=output_dir,
            root=root,
            wait=wait,
            preflight_checker=(
                suspended_plan_preflight
            ),
        )

    finally:
        resume_command = control_executor(
            action="resume_exports",
            root=root,
            wait=wait,
        )

        if not resume_command.successful:
            raise RuntimeError(
                "resume_exports was not reported as successfully "
                f"processed; exit code={resume_command.return_code}. "
                "Scanner exports may remain suspended."
            )

        resumed_preflight = preflight_checker(
            root=root,
        )

        if not resumed_preflight.ready:
            raise RuntimeError(
                "Scanner did not return to the expected active-export "
                f"state after resume_exports: {resumed_preflight.detail}"
            )

    assert resume_command is not None
    assert resumed_preflight is not None

    return CoordinatedWatchlistApplication(
        application=application,
        preflight_before=preflight_before,
        suspended_preflight=suspended_preflight,
        suspend_command=suspend_command,
        resume_command=resume_command,
        resumed_preflight=resumed_preflight,
    )
