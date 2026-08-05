"""Cross-platform execution lock for one-shot Watchlist cycle runs."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO


LOCK_STATE_ACTIVE = "active"
LOCK_STATE_RELEASED = "released"


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


def default_watchlist_cycle_lock_path(
    output_dir: Path,
) -> Path:
    """Return the default execution-lock file for a cycle output directory."""

    return (
        output_dir
        .expanduser()
        .resolve(strict=False)
        / ".watchlist-cycle.lock"
    )


def watchlist_cycle_lock_metadata_path(
    lock_path: Path,
) -> Path:
    """Return the diagnostic metadata path associated with a lock file."""

    resolved_path = (
        lock_path
        .expanduser()
        .resolve(strict=False)
    )

    return resolved_path.with_name(
        resolved_path.name + ".json"
    )


def read_watchlist_cycle_lock_metadata(
    lock_path: Path,
) -> dict[str, Any] | None:
    """Read lock metadata when it is available and valid."""

    metadata_path = (
        watchlist_cycle_lock_metadata_path(
            lock_path
        )
    )

    try:
        raw_text = metadata_path.read_text(
            encoding="utf-8"
        )
        data = json.loads(raw_text)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None

    if not isinstance(data, dict):
        return None

    return data


class WatchlistCycleLockHeldError(RuntimeError):
    """Raised when another process currently owns the cycle lock."""

    def __init__(
        self,
        lock_path: Path,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.lock_path = (
            lock_path
            .expanduser()
            .resolve(strict=False)
        )
        self.metadata = metadata

        detail = (
            f"Watchlist cycle execution lock is already held: "
            f"{self.lock_path}"
        )

        if metadata:
            pid = metadata.get("pid")
            hostname = metadata.get("hostname")
            acquired_at = metadata.get("acquired_at")

            owner_parts = [
                part
                for part in (
                    f"pid={pid}" if pid is not None else None,
                    (
                        f"host={hostname}"
                        if hostname
                        else None
                    ),
                    (
                        f"acquired_at={acquired_at}"
                        if acquired_at
                        else None
                    ),
                )
                if part is not None
            ]

            if owner_parts:
                detail += " (" + ", ".join(owner_parts) + ")"

        super().__init__(detail)


def _lock_file_nonblocking(
    file_handle: BinaryIO,
) -> None:
    """Acquire one nonblocking exclusive OS-level file lock."""

    file_handle.seek(0)

    if os.name == "nt":
        import msvcrt

        msvcrt.locking(
            file_handle.fileno(),
            msvcrt.LK_NBLCK,
            1,
        )
        return

    import fcntl

    fcntl.flock(
        file_handle.fileno(),
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )


def _unlock_file(
    file_handle: BinaryIO,
) -> None:
    """Release the platform-specific file lock."""

    file_handle.seek(0)

    if os.name == "nt":
        import msvcrt

        msvcrt.locking(
            file_handle.fileno(),
            msvcrt.LK_UNLCK,
            1,
        )
        return

    import fcntl

    fcntl.flock(
        file_handle.fileno(),
        fcntl.LOCK_UN,
    )


class WatchlistCycleLock:
    """
    Hold one OS-level lock for the duration of a Watchlist cycle invocation.

    The lock file and its adjacent JSON metadata file are intentionally
    retained after release. The operating-system lock—not either file's mere
    existence—determines whether another process is active. An unexpected
    process exit therefore releases the lock automatically.
    """

    def __init__(
        self,
        lock_path: Path,
        *,
        command: Sequence[str] = (),
        now_provider: Callable[[], datetime] = _utc_now,
        pid: int | None = None,
        hostname: str | None = None,
    ) -> None:
        self.path = (
            lock_path
            .expanduser()
            .resolve(strict=False)
        )
        self.metadata_path = (
            watchlist_cycle_lock_metadata_path(
                self.path
            )
        )
        self.command = tuple(command)
        self._now_provider = now_provider
        self._pid = os.getpid() if pid is None else pid
        self._hostname = (
            socket.gethostname()
            if hostname is None
            else hostname
        )
        self._file_handle: BinaryIO | None = None
        self._acquired_at: datetime | None = None

    @property
    def acquired(self) -> bool:
        """Whether this object currently owns the execution lock."""

        return self._file_handle is not None

    def _write_metadata(
        self,
        *,
        state: str,
        released_at: datetime | None = None,
    ) -> None:
        if not self.acquired:
            raise RuntimeError(
                "Cannot write metadata without an acquired lock."
            )

        record: dict[str, Any] = {
            "state": state,
            "pid": self._pid,
            "hostname": self._hostname,
            "acquired_at": (
                self._acquired_at.isoformat(
                    timespec="seconds"
                )
                if self._acquired_at is not None
                else None
            ),
            "command": list(self.command),
            "released_at": (
                released_at.isoformat(
                    timespec="seconds"
                )
                if released_at is not None
                else None
            ),
        }

        temporary_path = self.metadata_path.with_name(
            self.metadata_path.name
            + f".{self._pid}.tmp"
        )

        temporary_path.write_text(
            json.dumps(
                record,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        os.replace(
            temporary_path,
            self.metadata_path,
        )

    def acquire(self) -> WatchlistCycleLock:
        """Acquire the lock without waiting."""

        if self.acquired:
            raise RuntimeError(
                "This Watchlist cycle lock is already acquired."
            )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        file_handle = os.fdopen(
            descriptor,
            "r+b",
        )

        try:
            file_handle.seek(0, os.SEEK_END)

            if file_handle.tell() == 0:
                file_handle.write(b"\0")
                file_handle.flush()

            file_handle.seek(0)
            _lock_file_nonblocking(file_handle)
        except OSError as exc:
            file_handle.close()

            raise WatchlistCycleLockHeldError(
                self.path,
                read_watchlist_cycle_lock_metadata(
                    self.path
                ),
            ) from exc

        self._file_handle = file_handle
        self._acquired_at = self._now_provider()

        try:
            if (
                self._acquired_at.tzinfo is None
                or self._acquired_at.utcoffset() is None
            ):
                raise ValueError(
                    "Watchlist cycle lock time must include "
                    "timezone information."
                )

            self._write_metadata(
                state=LOCK_STATE_ACTIVE
            )
        except Exception:
            self.release()
            raise

        return self

    def release(self) -> None:
        """Release the lock and retain released-state metadata."""

        file_handle = self._file_handle

        if file_handle is None:
            return

        try:
            released_at = self._now_provider()

            if (
                released_at.tzinfo is not None
                and released_at.utcoffset() is not None
            ):
                self._write_metadata(
                    state=LOCK_STATE_RELEASED,
                    released_at=released_at,
                )
        finally:
            try:
                _unlock_file(file_handle)
            finally:
                file_handle.close()
                self._file_handle = None

    def __enter__(self) -> WatchlistCycleLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.release()
        return False
