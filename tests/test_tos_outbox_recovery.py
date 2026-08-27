from pathlib import Path

from schwab_watchlists.tos_outbox_recovery import (
    OutboxRecoveryConfig,
    default_recovery_config,
    run_recovery_loop,
    run_recovery_pass,
)
from schwab_watchlists.tos_watchlist_transport import (
    OutboxDrainResult,
)


def test_default_recovery_config_uses_mb_paths(
    monkeypatch,
):
    monkeypatch.setenv(
        "MB_SCAN_CONTROL",
        r"\\El-Cheapo\SCANCTRL",
    )

    monkeypatch.setenv(
        "MB_SCANS",
        r"C:\MasterBot\scans",
    )

    config = default_recovery_config()

    assert config.source_dir == Path(
        r"\\El-Cheapo\SCANCTRL"
    ) / "outgoing" / "watchlist_verify"

    assert config.destination_dir == Path(
        r"C:\MasterBot\scans"
    ) / "watchlist_verify"


def test_run_recovery_pass_uses_configured_paths(
    tmp_path,
):
    source_dir = (
        tmp_path
        / "outbox"
    )

    destination_dir = (
        tmp_path
        / "destination"
    )

    config = OutboxRecoveryConfig(
        source_dir=source_dir,
        destination_dir=destination_dir,
        transport_attempts=4,
        transport_retry_seconds=2.5,
    )

    calls = []

    expected = OutboxDrainResult(
        transported=(),
        already_present=(),
        failed=(),
    )

    def fake_drainer(
        source,
        destination,
        **kwargs,
    ):
        calls.append(
            (
                source,
                destination,
                kwargs,
            )
        )

        return expected

    result = run_recovery_pass(
        config,
        drainer=fake_drainer,
    )

    assert result is expected

    assert calls == [
        (
            source_dir,
            destination_dir,
            {
                "attempts": 4,
                "retry_seconds": 2.5,
            },
        )
    ]


def test_recovery_loop_retries_after_failed_pass(
    tmp_path,
):
    config = OutboxRecoveryConfig(
        source_dir=tmp_path / "outbox",
        destination_dir=(
            tmp_path
            / "destination"
        ),
        poll_seconds=7.5,
    )

    results = [
        OutboxDrainResult(
            transported=(),
            already_present=(),
            failed=(
                (
                    Path("first.csv"),
                    "network unavailable",
                ),
            ),
        ),
        OutboxDrainResult(
            transported=(
                Path("first.csv"),
            ),
            already_present=(),
            failed=(),
        ),
    ]

    drainer_calls = []
    sleep_calls = []
    observed_results = []

    def fake_drainer(
        source,
        destination,
        **kwargs,
    ):
        drainer_calls.append(
            (
                source,
                destination,
            )
        )

        return results[
            len(drainer_calls) - 1
        ]

    pass_count = run_recovery_loop(
        config,
        drainer=fake_drainer,
        sleep=sleep_calls.append,
        on_result=(
            observed_results.append
        ),
        max_passes=2,
    )

    assert pass_count == 2

    assert len(
        drainer_calls
    ) == 2

    assert sleep_calls == [
        7.5,
    ]

    assert observed_results == results
