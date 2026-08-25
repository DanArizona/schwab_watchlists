from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mb_watchlist_coordinator.adapter_state import (
    AdapterObservedState,
)
from mb_watchlist_coordinator.execution import (
    AdapterObservationResult,
    MaterializationExecutionResult,
)
from mb_watchlist_coordinator.health import (
    AdapterHealthState,
    AdapterHealthStatus,
)
from mb_watchlist_coordinator.transactions import (
    MaterializationTransaction,
)

from .tos_watchlist_transport import (
    read_watchlist_symbols,
    resume_exports_with_retry,
    run_watchlist_export,
    scanner_state_matches,
    wait_for_file,
    wait_for_scanner_state,
)


@dataclass(slots=True)
class LiveToSExecutor:
    root: Path | None
    verification_dir: Path
    wait: float

    preflight_checker: Callable[..., Any]
    control_executor: Callable[..., Any]

    scanner_state_wait: float = 10.0
    state_poll_seconds: float = 0.25
    verify_wait: float = 45.0
    resume_attempts: int = 3
    resume_retry_seconds: float = 1.0

    def observe(self) -> AdapterObservationResult:
        """
        Obtain one fresh, explicit ThinkOrSwim Watchlist observation.

        Scheduled exports remain suspended until the explicit Watchlist
        export has been read on MasterBot.
        """

        preflight = self.preflight_checker(
            root=self.root,
        )

        if not preflight.ready:
            raise RuntimeError(
                "Scanner preflight failed before "
                f"Watchlist observation: {preflight.detail}"
            )

        self.verification_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        target_filename = (
            self._build_observation_filename()
        )

        verification_path = (
            self.verification_dir
            / target_filename
        )

        if verification_path.exists():
            raise RuntimeError(
                "Refusing to reuse an existing "
                f"Watchlist observation file: {verification_path}"
            )

        suspend_attempted = False
        observed_state: AdapterObservedState | None = None

        try:
            suspend_attempted = True

            suspend_result = self.control_executor(
                action="suspend_exports",
                root=self.root,
                wait=self.wait,
            )

            if not suspend_result.successful:
                raise RuntimeError(
                    "suspend_exports was not reported "
                    "as successful; "
                    f"exit code={suspend_result.return_code}."
                )

            suspended = wait_for_scanner_state(
                root=self.root,
                expect_suspended=True,
                timeout=self.scanner_state_wait,
                preflight_checker=self.preflight_checker,
                state_poll_seconds=self.state_poll_seconds,
            )

            if not scanner_state_matches(
                suspended,
                expect_suspended=True,
            ):
                raise RuntimeError(
                    "Scanner did not enter the expected "
                    "suspended-export state."
                )

            _, export_return_code = run_watchlist_export(
                target_filename=target_filename,
                root=self.root,
                wait=self.wait,
            )

            if export_return_code != 0:
                raise RuntimeError(
                    "export_wl was not reported as successful; "
                    f"exit code={export_return_code}."
                )

            if not wait_for_file(
                verification_path,
                timeout=self.verify_wait,
            ):
                raise RuntimeError(
                    "Watchlist observation CSV did not appear: "
                    f"{verification_path}"
                )

            symbols = read_watchlist_symbols(
                verification_path
            )

            observed_state = AdapterObservedState(
                adapter_id="tos",
                symbols=frozenset(symbols),
                observed_at=datetime.now().astimezone(),
                evidence_ref=str(verification_path),
            )

        finally:
            if suspend_attempted:
                try:
                    self._resume_exports()
                except RuntimeError as exc:
                    if observed_state is not None:
                        return AdapterObservationResult(
                            observed_state=observed_state,
                            health_state=AdapterHealthState(
                                adapter_id="tos",
                                status=(
                                    AdapterHealthStatus.DEGRADED
                                ),
                                observed_at=(
                                    datetime.now().astimezone()
                                ),
                                reason=str(exc),
                                evidence_ref=str(
                                    verification_path
                                ),
                            ),
                        )

                    raise

        assert observed_state is not None

        return AdapterObservationResult(
            observed_state=observed_state,
            health_state=AdapterHealthState(
                adapter_id="tos",
                status=AdapterHealthStatus.HEALTHY,
                observed_at=datetime.now().astimezone(),
                evidence_ref=str(verification_path),
            ),
        )

    def materialize(
        self,
        transaction: MaterializationTransaction,
    ) -> MaterializationExecutionResult:
        raise NotImplementedError(
            "Live ToS materialization is the next integration step."
        )

    def _resume_exports(self) -> Any:
        def state_waiter(
            *,
            root: Path | None,
            expect_suspended: bool,
        ) -> Any:
            return wait_for_scanner_state(
                root=root,
                expect_suspended=expect_suspended,
                timeout=self.scanner_state_wait,
                preflight_checker=self.preflight_checker,
                state_poll_seconds=self.state_poll_seconds,
            )

        return resume_exports_with_retry(
            root=self.root,
            wait=self.wait,
            control_executor=self.control_executor,
            state_waiter=state_waiter,
            attempts=self.resume_attempts,
            retry_seconds=self.resume_retry_seconds,
        )

    @staticmethod
    def _build_observation_filename() -> str:
        suffix = uuid4().hex[:8]

        return (
            "COORD-OBS-"
            f"{suffix}-WL.csv"
        )
