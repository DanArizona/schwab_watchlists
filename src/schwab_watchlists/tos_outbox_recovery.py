from __future__ import annotations

import argparse
import os
import time

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tos_watchlist_transport import (
    OutboxDrainResult,
    drain_staged_watchlist_evidence,
)


@dataclass(frozen=True, slots=True)
class OutboxRecoveryConfig:
    source_dir: Path
    destination_dir: Path

    transport_attempts: int = 3
    transport_retry_seconds: float = 1.0
    poll_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.transport_attempts <= 0:
            raise ValueError(
                "transport_attempts must be positive."
            )

        if self.transport_retry_seconds < 0:
            raise ValueError(
                "transport_retry_seconds cannot be negative."
            )

        if self.poll_seconds <= 0:
            raise ValueError(
                "poll_seconds must be positive."
            )


def default_recovery_config(
    *,
    poll_seconds: float = 5.0,
    transport_attempts: int = 3,
    transport_retry_seconds: float = 1.0,
) -> OutboxRecoveryConfig:
    scan_control = os.environ.get(
        "MB_SCAN_CONTROL"
    )

    scans = os.environ.get(
        "MB_SCANS"
    )

    if not scan_control:
        raise RuntimeError(
            "MB_SCAN_CONTROL is not set."
        )

    if not scans:
        raise RuntimeError(
            "MB_SCANS is not set."
        )

    source_dir = (
        Path(scan_control)
        / "outgoing"
        / "watchlist_verify"
    )

    destination_dir = (
        Path(scans)
        / "watchlist_verify"
    )

    return OutboxRecoveryConfig(
        source_dir=source_dir,
        destination_dir=destination_dir,
        transport_attempts=transport_attempts,
        transport_retry_seconds=(
            transport_retry_seconds
        ),
        poll_seconds=poll_seconds,
    )


def run_recovery_pass(
    config: OutboxRecoveryConfig,
    *,
    drainer: Callable[..., OutboxDrainResult] = (
        drain_staged_watchlist_evidence
    ),
) -> OutboxDrainResult:
    """
    Perform one non-GUI recovery pass.
    """

    return drainer(
        config.source_dir,
        config.destination_dir,
        attempts=config.transport_attempts,
        retry_seconds=(
            config.transport_retry_seconds
        ),
    )


def run_recovery_loop(
    config: OutboxRecoveryConfig,
    *,
    drainer: Callable[..., OutboxDrainResult] = (
        drain_staged_watchlist_evidence
    ),
    sleep: Callable[[float], None] = time.sleep,
    on_result: Callable[
        [OutboxDrainResult],
        Any,
    ]
    | None = None,
    max_passes: int | None = None,
) -> int:
    """
    Repeatedly drain the Watchlist evidence outbox.

    A failed pass does not terminate the loop. This is what
    allows a sticky transport outage to heal automatically
    when connectivity returns.

    max_passes exists mainly for deterministic testing.
    None means run until interrupted.
    """

    if (
        max_passes is not None
        and max_passes <= 0
    ):
        raise ValueError(
            "max_passes must be positive or None."
        )

    pass_count = 0

    while (
        max_passes is None
        or pass_count < max_passes
    ):
        result = run_recovery_pass(
            config,
            drainer=drainer,
        )

        pass_count += 1

        if on_result is not None:
            on_result(result)

        if (
            max_passes is not None
            and pass_count >= max_passes
        ):
            break

        sleep(
            config.poll_seconds
        )

    return pass_count


def print_recovery_result(
    result: OutboxDrainResult,
) -> None:
    transported_count = len(
        result.transported
    )

    failed_count = len(
        result.failed
    )

    #
    # Avoid printing noise every five seconds when the
    # outbox is already fully synchronized.
    #
    if (
        transported_count == 0
        and failed_count == 0
    ):
        return

    print(
        "Watchlist outbox recovery: "
        f"transported={transported_count}, "
        f"already_present="
        f"{len(result.already_present)}, "
        f"failed={failed_count}"
    )

    for path in result.transported:
        print(
            f"  recovered: {path.name}"
        )

    for path, reason in result.failed:
        print(
            f"  failed   : {path}"
        )
        print(
            f"             {reason}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Continuously recover staged ThinkOrSwim "
            "Watchlist evidence from El-Cheapo."
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one recovery pass and exit.",
    )

    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help=(
            "Seconds between recovery passes "
            "(default: 5)."
        ),
    )

    parser.add_argument(
        "--transport-attempts",
        type=int,
        default=3,
        help=(
            "Copy attempts within each recovery pass "
            "(default: 3)."
        ),
    )

    parser.add_argument(
        "--transport-retry-seconds",
        type=float,
        default=1.0,
        help=(
            "Delay between copy attempts "
            "(default: 1)."
        ),
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    config = default_recovery_config(
        poll_seconds=args.poll_seconds,
        transport_attempts=(
            args.transport_attempts
        ),
        transport_retry_seconds=(
            args.transport_retry_seconds
        ),
    )

    print(
        f"Watchlist outbox : {config.source_dir}"
    )
    print(
        f"Destination      : {config.destination_dir}"
    )

    if args.once:
        result = run_recovery_pass(
            config
        )

        print_recovery_result(
            result
        )

        if result.successful:
            print(
                "Recovery pass    : SUCCESS"
            )
        else:
            print(
                "Recovery pass    : INCOMPLETE"
            )

        return

    print(
        f"Poll interval    : "
        f"{config.poll_seconds:.1f} seconds"
    )
    print(
        "Recovery loop running. "
        "Press Ctrl+C to stop."
    )

    try:
        run_recovery_loop(
            config,
            on_result=print_recovery_result,
        )

    except KeyboardInterrupt:
        print()
        print(
            "Recovery loop stopped."
        )


if __name__ == "__main__":
    main()
