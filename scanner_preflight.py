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
    exports_suspended: bool = False

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
            "exports_suspended": self.exports_suspended,
            "age_seconds": self.age_seconds,
        }


def check_scanner_ready(
    *,
    root: Path | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    allow_exports_suspended: bool = False,
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
    Require a current, running, and unpaused scanner.

    By default, the scanner must also be idle and exports must be active.
    When allow_exports_suspended=True, the intentionally suspended export
    state is also accepted. This is used while applying a Watchlist change
    under the shared export gate.
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
    exports_suspended = bool(
        payload.get("exports_suspended", False)
    )

    ordinary_ready = (
        report.status == "HEALTHY"
        and loop_state == "idle"
        and running
        and not paused
        and not exports_suspended
    )

    suspended_ready = (
        allow_exports_suspended
        and report.status == "HEALTHY"
        and loop_state == "exports_suspended"
        and running
        and not paused
        and exports_suspended
    )

    ready = ordinary_ready or suspended_ready

    if ordinary_ready:
        detail = (
            "Scanner is healthy, idle, running, "
            "not paused, and exports are active."
        )
    elif suspended_ready:
        detail = (
            "Scanner is healthy, running, not paused, "
            "and exports are suspended."
        )
    else:
        detail = (
            "Scanner is not ready for Watchlist submission: "
            f"status={report.status}, "
            f"loop_state={loop_state or '(missing)'}, "
            f"running={running}, "
            f"paused={paused}, "
            f"exports_suspended={exports_suspended}, "
            f"allow_exports_suspended={allow_exports_suspended}."
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
        exports_suspended=exports_suspended,
    )
