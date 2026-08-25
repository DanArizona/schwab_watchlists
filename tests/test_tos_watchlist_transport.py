from pathlib import Path
from types import SimpleNamespace

from schwab_watchlists.tos_watchlist_transport import (
    build_watchlist_export_command,
    read_watchlist_symbols,
    scanner_state_matches,
    wait_for_file,
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
