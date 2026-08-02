from pathlib import Path

import pytest

from mb_tools.scan_command import ScanCommandError
from mb_tools.scan_status import ScanStatusReport

from scanner_preflight import check_scanner_ready


def make_report(
    root: Path,
    *,
    status: str = "HEALTHY",
    loop_state: str = "idle",
    running: bool = True,
    paused: bool = False,
) -> ScanStatusReport:
    return ScanStatusReport(
        status=status,
        heartbeat_path=(
            root
            / "status"
            / "scanner_heartbeat.json"
        ),
        detail="Synthetic scanner status.",
        age_seconds=1.25,
        payload={
            "loop_state": loop_state,
            "running": running,
            "paused": paused,
        },
    )


def test_ready_scanner_passes(
    tmp_path: Path,
) -> None:
    def fake_resolver(
        root: Path | None,
    ) -> Path:
        assert root is None
        return tmp_path

    def fake_reader(
        *,
        root: Path,
        stale_after_s: float,
    ) -> ScanStatusReport:
        assert root == tmp_path
        assert stale_after_s == 30.0
        return make_report(root)

    result = check_scanner_ready(
        root_resolver=fake_resolver,
        status_reader=fake_reader,
    )

    assert result.ready
    assert result.status == "HEALTHY"
    assert result.loop_state == "idle"
    assert result.running
    assert not result.paused
    assert result.root == tmp_path


def test_scanner_not_running_is_rejected(
    tmp_path: Path,
) -> None:
    result = check_scanner_ready(
        root=tmp_path,
        root_resolver=lambda root: tmp_path,
        status_reader=lambda **kwargs: make_report(
            tmp_path,
            running=False,
        ),
    )

    assert not result.ready
    assert "running=False" in result.detail


def test_paused_scanner_is_rejected(
    tmp_path: Path,
) -> None:
    result = check_scanner_ready(
        root=tmp_path,
        root_resolver=lambda root: tmp_path,
        status_reader=lambda **kwargs: make_report(
            tmp_path,
            status="PAUSED",
            loop_state="paused",
            running=True,
            paused=True,
        ),
    )

    assert not result.ready
    assert result.status == "PAUSED"
    assert result.paused


def test_command_root_error_is_wrapped() -> None:
    def failing_resolver(
        root: Path | None,
    ) -> Path:
        raise ScanCommandError(
            "MB_SCAN_CONTROL is not configured."
        )

    with pytest.raises(
        RuntimeError,
        match="Could not resolve scanner command root",
    ):
        check_scanner_ready(
            root_resolver=failing_resolver,
        )
