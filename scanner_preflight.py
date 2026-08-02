"""
Scanner-readiness checks for live ThinkOrSwim Watchlist submission.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mb_tools.scan_command import (
    ScanCommandError,
    resolve_command_root,
)
from mb_tools.scan_status import (
    DEFAULT_STALE_AFTER_S,
    ScanStatusReport,
    read_scan_status,
)


@dataclass(frozen=True, slots=True)
class ScannerPreflightResult:
    """Result of one scanner-readiness check."""

    root: Path
    ready: bool
    status: str
    detail: str
    loop_state: str
    running: bool
    paused: bool
    age_seconds: float | None

    def as_record(self) -> dict[str, Any]:
        """Return JSON-compatible preflight metadata."""

        return {
            "root": str(self.root),
            "ready": self.ready,
            "status": self.status,
            "detail": self.detail,
            "loop_state": self.loop_state,
            "running": self.running,
            "paused": self.paused,
            "age_seconds": self.age_seconds,
        }


def check_scanner_ready(
    *,
    root: Path | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    root_resolver: Callable[
        [Path | None],
        Path,
    ] = resolve_command_root,
    status_reader: Callable[
        ...,
        ScanStatusReport,
    ] = read_scan_status,
) -> ScannerPreflightResult:
    """
    Require a current, idle, running, and unpaused scanner.

    This is intentionally stricter than the general mb-scan-status
    definition of an operational scanner.
    """

    if stale_after_s <= 0:
        raise ValueError(
            "Scanner stale-after value must be greater than zero."
        )

    try:
        resolved_root = root_resolver(root)
    except ScanCommandError as exc:
        raise RuntimeError(
            f"Could not resolve scanner command root: {exc}"
        ) from exc

    report = status_reader(
        root=resolved_root,
        stale_after_s=stale_after_s,
    )

    payload = report.payload or {}

    loop_state = str(
        payload.get("loop_state", "")
    ).strip().lower()

    running = bool(payload.get("running", False))
    paused = bool(payload.get("paused", False))

    ready = (
        report.status == "HEALTHY"
        and loop_state == "idle"
        and running
        and not paused
    )

    if ready:
        detail = (
            "Scanner is healthy, idle, running, "
            "and not paused."
        )
    else:
        detail = (
            "Scanner is not ready for Watchlist submission: "
            f"status={report.status}, "
            f"loop_state={loop_state or '(missing)'}, "
            f"running={running}, "
            f"paused={paused}."
        )

    return ScannerPreflightResult(
        root=resolved_root,
        ready=ready,
        status=report.status,
        detail=detail,
        loop_state=loop_state,
        running=running,
        paused=paused,
        age_seconds=report.age_seconds,
    )
