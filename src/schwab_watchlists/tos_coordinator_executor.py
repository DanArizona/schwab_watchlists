from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
from uuid import uuid4

from mb_watchlist_coordinator.adapter_state import (
    AdapterObservedState,
)
from mb_watchlist_coordinator.execution import (
    AdapterObservationResult,
    MaterializationExecutionResult,
    MaterializationExecutionStatus,
)
from mb_watchlist_coordinator.health import (
    AdapterHealthState,
    AdapterHealthStatus,
)
from mb_watchlist_coordinator.transactions import (
    MaterializationTransaction,
    MaterializationTransactionStatus,
)

from .tos_watchlist_transport import (
    read_watchlist_symbols,
    resume_exports_with_retry,
    run_watchlist_export,
    scanner_state_matches,
    wait_for_file,
    wait_for_scanner_state,
)

EASTERN = ZoneInfo("America/New_York")

@dataclass(slots=True)
class LiveToSExecutor:
    root: Path | None
    verification_dir: Path
    wait: float

    preflight_checker: Callable[..., Any]
    control_executor: Callable[..., Any]
    output_dir: Path | None = None
    submitter: Callable[..., Any] | None = None

    scanner_state_wait: float = 10.0
    state_poll_seconds: float = 0.25
    verify_wait: float = 45.0
    verify_attempts: int = 2
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
                observed_at=datetime.now(EASTERN),
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
                                    datetime.now(EASTERN)
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
                observed_at=datetime.now(EASTERN),
                evidence_ref=str(verification_path),
            ),
        )

    def materialize(
        self,
        transaction: MaterializationTransaction,
    ) -> MaterializationExecutionResult:
        """
        Apply one ADD or REPLACE transaction and obtain a fresh,
        complete Watchlist observation.

        Full-target satisfaction is deliberately NOT decided here.
        The coordinator verifies the returned observation against the
        immutable transaction target.
        """

        if transaction.adapter_id != "tos":
            raise ValueError(
                "LiveToSExecutor requires a ToS transaction."
            )

        if (
            transaction.status
            is not MaterializationTransactionStatus.ACTIVE
        ):
            raise ValueError(
                "LiveToSExecutor requires an ACTIVE transaction."
            )

        operation = transaction.operation.upper()

        if operation not in {"ADD", "REPLACE"}:
            raise ValueError(
                "Unsupported ToS materialization operation: "
                f"{transaction.operation!r}"
            )

        if self.submitter is None:
            raise RuntimeError(
                "Live ToS materialization requires a submitter."
            )

        if self.output_dir is None:
            raise RuntimeError(
                "Live ToS materialization requires output_dir."
            )

        try:
            preflight = self.preflight_checker(
                root=self.root,
            )
        except Exception as exc:
            return MaterializationExecutionResult(
                transaction_id=transaction.transaction_id,
                status=MaterializationExecutionStatus.FAILED,
                reason=(
                    "Scanner preflight failed before "
                    f"Watchlist materialization: {exc}"
                ),
            )

        if not preflight.ready:
            return MaterializationExecutionResult(
                transaction_id=transaction.transaction_id,
                status=MaterializationExecutionStatus.FAILED,
                reason=(
                    "Scanner preflight failed before "
                    "Watchlist materialization: "
                    f"{preflight.detail}"
                ),
            )

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.verification_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        target_filename = (
            self._build_materialization_filename()
        )

        verification_path = (
            self.verification_dir
            / target_filename
        )

        if verification_path.exists():
            return MaterializationExecutionResult(
                transaction_id=transaction.transaction_id,
                status=MaterializationExecutionStatus.FAILED,
                reason=(
                    "Refusing to reuse an existing "
                    "Watchlist materialization evidence file: "
                    f"{verification_path}"
                ),
            )

        execution_result: (
            MaterializationExecutionResult | None
        ) = None

        suspend_attempted = False

        try:
            suspend_attempted = True

            try:
                suspend_result = self.control_executor(
                    action="suspend_exports",
                    root=self.root,
                    wait=self.wait,
                )
            except Exception as exc:
                execution_result = MaterializationExecutionResult(
                    transaction_id=transaction.transaction_id,
                    status=MaterializationExecutionStatus.FAILED,
                    reason=(
                        "Could not suspend scheduled exports "
                        f"before Watchlist mutation: {exc}"
                    ),
                )

            else:
                if not suspend_result.successful:
                    execution_result = (
                        MaterializationExecutionResult(
                            transaction_id=(
                                transaction.transaction_id
                            ),
                            status=(
                                MaterializationExecutionStatus.FAILED
                            ),
                            reason=(
                                "suspend_exports was not "
                                "reported as successful; "
                                f"exit code="
                                f"{suspend_result.return_code}."
                            ),
                        )
                    )

                else:
                    try:
                        suspended = wait_for_scanner_state(
                            root=self.root,
                            expect_suspended=True,
                            timeout=self.scanner_state_wait,
                            preflight_checker=(
                                self.preflight_checker
                            ),
                            state_poll_seconds=(
                                self.state_poll_seconds
                            ),
                        )
                    except Exception as exc:
                        execution_result = (
                            MaterializationExecutionResult(
                                transaction_id=(
                                    transaction.transaction_id
                                ),
                                status=(
                                    MaterializationExecutionStatus.FAILED
                                ),
                                reason=(
                                    "Could not verify suspended "
                                    f"scanner state: {exc}"
                                ),
                            )
                        )

                    else:
                        if not scanner_state_matches(
                            suspended,
                            expect_suspended=True,
                        ):
                            execution_result = (
                                MaterializationExecutionResult(
                                    transaction_id=(
                                        transaction.transaction_id
                                    ),
                                    status=(
                                        MaterializationExecutionStatus.FAILED
                                    ),
                                    reason=(
                                        "Scanner did not enter "
                                        "the expected "
                                        "suspended-export state."
                                    ),
                                )
                            )

                        else:

                            def suspended_submission_preflight(
                                *,
                                root: Path | None,
                            ) -> Any:
                                result = (
                                    self.preflight_checker(
                                        root=root,
                                        allow_exports_suspended=True,
                                    )
                                )

                                if not scanner_state_matches(
                                    result,
                                    expect_suspended=True,
                                ):
                                    raise RuntimeError(
                                        "Scanner left the "
                                        "expected suspended-export "
                                        "state before Watchlist "
                                        "submission."
                                    )

                                return result

                            try:
                                submission = self.submitter(
                                    mode=operation.lower(),
                                    symbols=sorted(
                                        transaction.operation_symbols
                                    ),
                                    submit=True,
                                    wait=self.wait,
                                    root=self.root,
                                    output_dir=self.output_dir,
                                    preflight_checker=(
                                        suspended_submission_preflight
                                    ),
                                )

                            except Exception as exc:
                                # Once submitter() has been entered,
                                # conservatively assume that the
                                # external mutation may have begun.
                                execution_result = (
                                    MaterializationExecutionResult(
                                        transaction_id=(
                                            transaction.transaction_id
                                        ),
                                        status=(
                                            MaterializationExecutionStatus
                                            .OUTCOME_UNKNOWN
                                        ),
                                        reason=(
                                            "Watchlist mutation may "
                                            "have begun but did not "
                                            "return a usable result: "
                                            f"{exc}"
                                        ),
                                    )
                                )

                            else:
                                if not submission.submitted:
                                    execution_result = (
                                        MaterializationExecutionResult(
                                            transaction_id=(
                                                transaction.transaction_id
                                            ),
                                            status=(
                                                MaterializationExecutionStatus
                                                .FAILED
                                            ),
                                            reason=(
                                                "Watchlist mutation "
                                                "was not submitted."
                                            ),
                                        )
                                    )

                                elif not submission.successful:
                                    execution_result = (
                                        MaterializationExecutionResult(
                                            transaction_id=(
                                                transaction.transaction_id
                                            ),
                                            status=(
                                                MaterializationExecutionStatus
                                                .OUTCOME_UNKNOWN
                                            ),
                                            reason=(
                                                "Watchlist mutation "
                                                "was submitted but "
                                                "not reported as "
                                                "successful; "
                                                f"exit code="
                                                f"{submission.return_code}."
                                            ),
                                        )
                                    )
                                else:
                                    observed = None
                                    verification_error = None

                                    for verification_attempt in range(
                                        1,
                                        self.verify_attempts + 1,
                                    ):
                                        if verification_attempt > 1:
                                            target_filename = (
                                                self._build_materialization_filename()
                                            )

                                            verification_path = (
                                                self.verification_dir
                                                / target_filename
                                            )

                                        try:
                                            (
                                                _,
                                                export_return_code,
                                            ) = run_watchlist_export(
                                                target_filename=(
                                                    target_filename
                                                ),
                                                root=self.root,
                                                wait=self.wait,
                                            )

                                            if export_return_code != 0:
                                                raise RuntimeError(
                                                    "export_wl was not "
                                                    "reported as "
                                                    "successful; "
                                                    f"exit code="
                                                    f"{export_return_code}."
                                                )

                                            if not wait_for_file(
                                                verification_path,
                                                timeout=self.verify_wait,
                                            ):
                                                raise RuntimeError(
                                                    "Watchlist "
                                                    "materialization CSV "
                                                    "did not appear: "
                                                    f"{verification_path}"
                                                )

                                            symbols = (
                                                read_watchlist_symbols(
                                                    verification_path
                                                )
                                            )

                                        except Exception as exc:
                                            verification_error = exc

                                            if (
                                                verification_attempt
                                                < self.verify_attempts
                                            ):
                                                continue

                                        else:
                                            observed = (
                                                AdapterObservedState(
                                                    adapter_id="tos",
                                                    symbols=frozenset(
                                                        symbols
                                                    ),
                                                    observed_at=(
                                                        datetime.now(
                                                            EASTERN
                                                        )
                                                    ),
                                                    evidence_ref=str(
                                                        verification_path
                                                    ),
                                                )
                                            )

                                            break

                                    if observed is None:
                                        execution_result = (
                                            MaterializationExecutionResult(
                                                transaction_id=(
                                                    transaction
                                                    .transaction_id
                                                ),
                                                status=(
                                                    MaterializationExecutionStatus
                                                    .OUTCOME_UNKNOWN
                                                ),
                                                reason=(
                                                    "Watchlist mutation "
                                                    "was reported as "
                                                    "successful, but "
                                                    "a trustworthy "
                                                    "observation could "
                                                    "not be obtained "
                                                    f"after "
                                                    f"{self.verify_attempts} "
                                                    "verification "
                                                    "attempts; "
                                                    "last error: "
                                                    f"{verification_error}"
                                                ),
                                            )
                                        )

                                    else:
                                        execution_result = (
                                            MaterializationExecutionResult(
                                                transaction_id=(
                                                    transaction
                                                    .transaction_id
                                                ),
                                                status=(
                                                    MaterializationExecutionStatus
                                                    .OBSERVED
                                                ),
                                                observed_state=observed,
                                            )
                                        )

        finally:
            health: AdapterHealthState | None = None

            if suspend_attempted:
                try:
                    self._resume_exports()

                except Exception as exc:
                    health = AdapterHealthState(
                        adapter_id="tos",
                        status=(
                            AdapterHealthStatus.DEGRADED
                        ),
                        observed_at=datetime.now(EASTERN),
                        reason=str(exc),
                        evidence_ref=(
                            str(verification_path)
                            if verification_path.exists()
                            else None
                        ),
                    )

                else:
                    health = AdapterHealthState(
                        adapter_id="tos",
                        status=AdapterHealthStatus.HEALTHY,
                        observed_at=datetime.now(EASTERN),
                        evidence_ref=(
                            str(verification_path)
                            if verification_path.exists()
                            else None
                        ),
                    )

        if execution_result is None:
            raise RuntimeError(
                "Live ToS materialization ended without "
                "an execution result."
            )

        if health is None:
            return execution_result

        return MaterializationExecutionResult(
            transaction_id=execution_result.transaction_id,
            status=execution_result.status,
            observed_state=execution_result.observed_state,
            reason=execution_result.reason,
            health_state=health,
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
    def _build_materialization_filename() -> str:
        suffix = uuid4().hex[:8]

        return (
            "COORD-MAT-"
            f"{suffix}-WL.csv"
        )

    @staticmethod
    def _build_observation_filename() -> str:
        suffix = uuid4().hex[:8]

        return (
            "COORD-OBS-"
            f"{suffix}-WL.csv"
        )
