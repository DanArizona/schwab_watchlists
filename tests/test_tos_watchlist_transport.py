from pathlib import Path
from types import SimpleNamespace

from schwab_watchlists.tos_watchlist_transport import (
    build_watchlist_export_command,
    read_watchlist_symbols,
    scanner_state_matches,
    transport_staged_file,
    wait_for_file,
    resume_exports_with_retry,
    run_watchlist_export,
    wait_for_scanner_state,
    OutboxDrainResult,
    drain_staged_watchlist_evidence,
)


def test_build_watchlist_export_command(tmp_path):
    command = build_watchlist_export_command(
        target_filename="test-WL.csv",
        root=tmp_path,
        wait=30.0,
        executable="mb-scan-command",
    )

    assert command == (
        "mb-scan-command",
        "export_wl",
        "--target-filename",
        "test-WL.csv",
        "--root",
        str(tmp_path.resolve()),
        "--wait",
        "30.0",
    )


def test_read_watchlist_symbols_handles_tos_preamble(
    tmp_path,
):
    path = tmp_path / "watchlist.csv"

    path.write_text(
        "\ufeffWatchlist 'default'\n"
        "\n"
        "default\n"
        "Symbol,Last,Volume\n"
        "aapl,227.00,100\n"
        "NVDA,180.00,200\n",
        encoding="utf-8",
    )

    assert read_watchlist_symbols(path) == {
        "AAPL",
        "NVDA",
    }


def test_scanner_state_matches_expected_states():
    suspended = SimpleNamespace(
        ready=True,
        status="HEALTHY",
        loop_state="exports_suspended",
        running=True,
        paused=False,
        exports_suspended=True,
    )

    active = SimpleNamespace(
        ready=True,
        status="HEALTHY",
        loop_state="idle",
        running=True,
        paused=False,
        exports_suspended=False,
    )

    assert scanner_state_matches(
        suspended,
        expect_suspended=True,
    )

    assert scanner_state_matches(
        active,
        expect_suspended=False,
    )

    assert not scanner_state_matches(
        active,
        expect_suspended=True,
    )


def test_wait_for_file_returns_true_for_existing_file(
    tmp_path,
):
    path = tmp_path / "present.csv"
    path.write_text("test\n", encoding="utf-8")

    assert wait_for_file(
        path,
        timeout=1.0,
    )


def test_transport_staged_file_uses_atomic_final_name(
    tmp_path,
):
    source = (
        tmp_path
        / "outbox"
        / "verify-WL.csv"
    )

    destination = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
        / "verify-WL.csv"
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    result = transport_staged_file(
        source,
        destination,
        attempts=3,
        retry_seconds=0.0,
    )

    assert result == destination
    assert destination.exists()

    assert list(
        destination.parent.glob(
            destination.name + ".tmp.*"
        )
    ) == []

    assert destination.read_text(
        encoding="utf-8"
    ) == source.read_text(
        encoding="utf-8"
    )


def test_transport_staged_file_accepts_already_delivered_identical_file(
    tmp_path,
):
    source = (
        tmp_path
        / "outbox"
        / "verify-WL.csv"
    )

    destination = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
        / "verify-WL.csv"
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    destination.write_bytes(
        source.read_bytes()
    )

    copy_calls = []

    def should_not_copy(
        copy_source: Path,
        copy_destination: Path,
    ):
        copy_calls.append(
            (
                copy_source,
                copy_destination,
            )
        )

        raise AssertionError(
            "Identical evidence should not be copied again."
        )

    result = transport_staged_file(
        source,
        destination,
        attempts=1,
        retry_seconds=0.0,
        copier=should_not_copy,
    )

    assert result == destination
    assert copy_calls == []


def test_transport_staged_file_handles_concurrent_identical_delivery(
    tmp_path,
):
    source = (
        tmp_path
        / "outbox"
        / "verify-WL.csv"
    )

    destination = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
        / "verify-WL.csv"
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    concurrent_delivery_done = False

    def racing_copier(
        copy_source: Path,
        copy_destination: Path,
    ):
        nonlocal concurrent_delivery_done

        copy_destination.write_bytes(
            copy_source.read_bytes()
        )

        if not concurrent_delivery_done:
            concurrent_delivery_done = True

            #
            # Simulate mb-wl-recovery completing delivery while
            # the executor still has its own copy in progress.
            #
            transport_staged_file(
                source,
                destination,
                attempts=1,
                retry_seconds=0.0,
            )

    result = transport_staged_file(
        source,
        destination,
        attempts=1,
        retry_seconds=0.0,
        copier=racing_copier,
    )

    assert result == destination

    assert destination.read_bytes() == (
        source.read_bytes()
    )

    assert list(
        destination.parent.glob(
            destination.name + ".tmp.*"
        )
    ) == []


def test_transport_staged_file_rejects_conflicting_destination(
    tmp_path,
):
    source = (
        tmp_path
        / "outbox"
        / "verify-WL.csv"
    )

    destination = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
        / "verify-WL.csv"
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    destination.write_text(
        "Symbol\nAAPL\n",
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(
        RuntimeError,
        match="different contents",
    ):
        transport_staged_file(
            source,
            destination,
            attempts=1,
            retry_seconds=0.0,
        )

    #
    # Never overwrite conflicting evidence.
    #
    assert destination.read_text(
        encoding="utf-8"
    ) == "Symbol\nAAPL\n"


def test_transport_staged_file_retries_copy_without_tos(
    tmp_path,
):
    source = (
        tmp_path
        / "outbox"
        / "verify-WL.csv"
    )

    destination = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
        / "verify-WL.csv"
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    copy_calls = []
    sleep_calls = []

    def flaky_copier(
        copy_source: Path,
        copy_destination: Path,
    ):
        copy_calls.append(
            (
                copy_source,
                copy_destination,
            )
        )

        if len(copy_calls) == 1:
            raise OSError(
                "simulated transport failure"
            )

        copy_destination.write_bytes(
            copy_source.read_bytes()
        )

    result = transport_staged_file(
        source,
        destination,
        attempts=3,
        retry_seconds=0.5,
        copier=flaky_copier,
        sleep=sleep_calls.append,
    )

    assert result == destination
    assert destination.exists()

    assert len(copy_calls) == 2
    assert sleep_calls == [0.5]


def test_drain_staged_watchlist_evidence_transports_missing_and_skips_identical(
    tmp_path,
):
    source_dir = (
        tmp_path
        / "outbox"
    )

    destination_dir = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
    )

    source_dir.mkdir(
        parents=True,
    )

    destination_dir.mkdir(
        parents=True,
    )

    already_source = (
        source_dir
        / "COORD-OBS-aaaa-WL.csv"
    )

    missing_source = (
        source_dir
        / "COORD-MAT-bbbb-WL.csv"
    )

    already_source.write_text(
        "Symbol\nAAPL\n",
        encoding="utf-8",
    )

    missing_source.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    already_destination = (
        destination_dir
        / already_source.name
    )

    already_destination.write_bytes(
        already_source.read_bytes()
    )

    result = (
        drain_staged_watchlist_evidence(
            source_dir,
            destination_dir,
            attempts=3,
            retry_seconds=0.0,
        )
    )

    missing_destination = (
        destination_dir
        / missing_source.name
    )

    assert result.successful is True

    assert result.transported == (
        missing_destination,
    )

    assert result.already_present == (
        already_destination,
    )

    assert result.failed == ()

    assert (
        missing_destination.read_bytes()
        == missing_source.read_bytes()
    )


def test_drain_staged_watchlist_evidence_rejects_conflicting_destination(
    tmp_path,
):
    source_dir = tmp_path / "outbox"

    destination_dir = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
    )

    source_dir.mkdir(
        parents=True,
    )

    destination_dir.mkdir(
        parents=True,
    )

    source = (
        source_dir
        / "COORD-MAT-conflict-WL.csv"
    )

    destination = (
        destination_dir
        / source.name
    )

    source.write_text(
        "Symbol\nAAPL\n",
        encoding="utf-8",
    )

    destination.write_text(
        "Symbol\nNVDA\n",
        encoding="utf-8",
    )

    result = (
        drain_staged_watchlist_evidence(
            source_dir,
            destination_dir,
            attempts=3,
            retry_seconds=0.0,
        )
    )

    assert result.successful is False
    assert result.transported == ()
    assert result.already_present == ()

    assert len(result.failed) == 1

    failed_path, reason = (
        result.failed[0]
    )

    assert failed_path == source

    assert (
        "different contents"
        in reason
    )

    # Existing evidence was not overwritten.
    assert destination.read_text(
        encoding="utf-8"
    ) == "Symbol\nNVDA\n"


def test_drain_staged_watchlist_evidence_continues_after_transport_failure(
    tmp_path,
):
    source_dir = tmp_path / "outbox"

    destination_dir = (
        tmp_path
        / "masterbot"
        / "watchlist_verify"
    )

    source_dir.mkdir(
        parents=True,
    )

    bad_source = (
        source_dir
        / "COORD-MAT-aaaa-WL.csv"
    )

    good_source = (
        source_dir
        / "COORD-MAT-bbbb-WL.csv"
    )

    bad_source.write_text(
        "Symbol\nBAD\n",
        encoding="utf-8",
    )

    good_source.write_text(
        "Symbol\nGOOD\n",
        encoding="utf-8",
    )

    calls = []

    def fake_transporter(
        source,
        destination,
        **kwargs,
    ):
        calls.append(source.name)

        if source == bad_source:
            raise RuntimeError(
                "simulated sticky failure"
            )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination.write_bytes(
            source.read_bytes()
        )

        return destination

    result = (
        drain_staged_watchlist_evidence(
            source_dir,
            destination_dir,
            attempts=3,
            retry_seconds=0.0,
            transporter=fake_transporter,
        )
    )

    good_destination = (
        destination_dir
        / good_source.name
    )

    assert result.successful is False

    assert result.transported == (
        good_destination,
    )

    assert len(result.failed) == 1

    assert result.failed[0][0] == (
        bad_source
    )

    assert calls == [
        "COORD-MAT-aaaa-WL.csv",
        "COORD-MAT-bbbb-WL.csv",
    ]

    assert good_destination.exists()


def test_run_watchlist_export_uses_injected_command_builder(
    tmp_path,
):
    calls = []

    def command_builder(**kwargs):
        assert kwargs["target_filename"] == "verify-WL.csv"

        return (
            "mb-scan-command",
            "export_wl",
            "--target-filename",
            "verify-WL.csv",
        )

    def executable_finder(name):
        assert name == "mb-scan-command"
        return "mb-scan-command"

    def runner(command, **kwargs):
        calls.append((command, kwargs))

        return SimpleNamespace(
            returncode=0
        )

    command, return_code = run_watchlist_export(
        target_filename="verify-WL.csv",
        root=tmp_path,
        wait=30.0,
        command_builder=command_builder,
        executable_finder=executable_finder,
        runner=runner,
    )

    assert return_code == 0
    assert calls[0][0] == list(command)


def test_wait_for_scanner_state_returns_matching_state(
    tmp_path,
):
    active = SimpleNamespace(
        ready=True,
        status="HEALTHY",
        loop_state="idle",
        running=True,
        paused=False,
        exports_suspended=False,
    )

    result = wait_for_scanner_state(
        root=tmp_path,
        expect_suspended=False,
        timeout=1.0,
        preflight_checker=lambda **kwargs: active,
        state_poll_seconds=0.1,
    )

    assert result is active


def test_resume_exports_with_retry_returns_active_state(
    tmp_path,
):
    active = SimpleNamespace(
        ready=True,
        status="HEALTHY",
        loop_state="idle",
        running=True,
        paused=False,
        exports_suspended=False,
        detail="Scanner ready.",
    )

    control = SimpleNamespace(
        successful=True,
        return_code=0,
    )

    result = resume_exports_with_retry(
        root=tmp_path,
        wait=30.0,
        control_executor=lambda **kwargs: control,
        state_waiter=lambda **kwargs: active,
        attempts=3,
        retry_seconds=0.0,
    )

    assert result is active
