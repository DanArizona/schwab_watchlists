from pathlib import Path
from types import SimpleNamespace

from schwab_watchlists.tos_watchlist_transport import (
    build_watchlist_export_command,
    read_watchlist_symbols,
    scanner_state_matches,
    wait_for_file,
    resume_exports_with_retry,
    run_watchlist_export,
    wait_for_scanner_state,
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
