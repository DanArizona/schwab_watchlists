from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import run_scheduled_watchlist_cycle
from watchlist_cycle_lock import (
    LOCK_STATE_ACTIVE,
    LOCK_STATE_RELEASED,
    WatchlistCycleLock,
    WatchlistCycleLockHeldError,
    default_watchlist_cycle_lock_path,
    read_watchlist_cycle_lock_metadata,
)


LOCK_TIME = datetime(
    2026,
    8,
    5,
    13,
    30,
    tzinfo=timezone.utc,
)

RELEASE_TIME = datetime(
    2026,
    8,
    5,
    13,
    31,
    tzinfo=timezone.utc,
)


def sequential_times():
    values = iter((LOCK_TIME, RELEASE_TIME))

    return lambda: next(values)


def test_second_lock_is_rejected(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "cycle.lock"

    first = WatchlistCycleLock(
        lock_path,
        command=("first",),
        now_provider=lambda: LOCK_TIME,
        pid=101,
        hostname="MASTERBOT",
    )

    with first:
        metadata = (
            read_watchlist_cycle_lock_metadata(
                lock_path
            )
        )

        assert metadata is not None
        assert metadata["state"] == (
            LOCK_STATE_ACTIVE
        )
        assert metadata["pid"] == 101

        with pytest.raises(
            WatchlistCycleLockHeldError
        ):
            WatchlistCycleLock(
                lock_path,
                command=("second",),
                now_provider=lambda: LOCK_TIME,
                pid=202,
                hostname="MASTERBOT",
            ).acquire()


def test_lock_can_be_reacquired_after_release(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "cycle.lock"

    first = WatchlistCycleLock(
        lock_path,
        now_provider=sequential_times(),
    )

    with first:
        assert first.acquired

    released = (
        read_watchlist_cycle_lock_metadata(
            lock_path
        )
    )

    assert released is not None
    assert released["state"] == (
        LOCK_STATE_RELEASED
    )
    assert released["released_at"] == (
        RELEASE_TIME.isoformat(
            timespec="seconds"
        )
    )

    second = WatchlistCycleLock(
        lock_path,
        now_provider=sequential_times(),
    )

    with second:
        assert second.acquired


def test_context_releases_after_exception(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "cycle.lock"

    with pytest.raises(
        RuntimeError,
        match="simulated failure",
    ):
        with WatchlistCycleLock(
            lock_path,
            now_provider=sequential_times(),
        ):
            raise RuntimeError(
                "simulated failure"
            )

    with WatchlistCycleLock(
        lock_path,
        now_provider=sequential_times(),
    ):
        pass


def test_default_lock_path_uses_output_dir(
    tmp_path: Path,
) -> None:
    assert default_watchlist_cycle_lock_path(
        tmp_path
    ) == (
        tmp_path.resolve()
        / ".watchlist-cycle.lock"
    )


def test_scheduled_runner_stops_when_lock_is_held(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = (
        tmp_path / ".watchlist-cycle.lock"
    )

    def unexpected_main(argv):
        raise AssertionError(
            "Cycle delegation was unexpected."
        )

    monkeypatch.setattr(
        run_scheduled_watchlist_cycle
        .run_watchlist_cycle,
        "main",
        unexpected_main,
    )

    with WatchlistCycleLock(
        lock_path,
        command=("existing-cycle",),
        now_provider=lambda: LOCK_TIME,
        pid=303,
        hostname="MASTERBOT",
    ):
        exit_code = (
            run_scheduled_watchlist_cycle.main(
                [
                    "--mode",
                    "replace",
                    "--output-dir",
                    str(tmp_path),
                ]
            )
        )

    captured = capsys.readouterr()

    assert exit_code == (
        run_scheduled_watchlist_cycle
        .LOCK_HELD_EXIT_CODE
    )
    assert (
        "Another Watchlist cycle invocation "
        "is already running"
        in captured.err
    )
    assert "Owner PID        : 303" in (
        captured.err
    )
    assert "No Watchlist cycle was started." in (
        captured.err
    )


def test_scheduled_runner_delegates_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "custom.lock"
    captured_argv: list[str] = []

    def fake_main(argv):
        captured_argv.extend(argv)
        return 17

    monkeypatch.setattr(
        run_scheduled_watchlist_cycle
        .run_watchlist_cycle,
        "main",
        fake_main,
    )

    exit_code = (
        run_scheduled_watchlist_cycle.main(
            [
                "--mode",
                "replace",
                "--output-dir",
                str(tmp_path),
                "--lock-file",
                str(lock_path),
            ]
        )
    )

    assert exit_code == 17
    assert "--lock-file" not in captured_argv
    assert str(lock_path) not in captured_argv

    metadata = (
        read_watchlist_cycle_lock_metadata(
            lock_path
        )
    )

    assert metadata is not None
    assert metadata["state"] == (
        LOCK_STATE_RELEASED
    )

    with WatchlistCycleLock(lock_path):
        pass
