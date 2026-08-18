from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_nasdaq_halt_watchlist as halt_wl


def test_resolve_verification_dir_uses_mb_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans_dir = tmp_path / "SCANS"

    monkeypatch.setenv(
        "MB_SCANS",
        str(scans_dir),
    )

    result = halt_wl.resolve_verification_dir(
        None
    )

    assert result == (
        scans_dir / "watchlist_verify"
    ).resolve()


def test_build_verification_filename_is_deterministic_with_fixed_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        halt_wl.uuid,
        "uuid4",
        lambda: SimpleNamespace(
            hex="abcdef0123456789"
        ),
    )

    poll_time = datetime(
        2026,
        8,
        18,
        14,
        30,
        45,
        tzinfo=halt_wl.ET_ZONE,
    )

    filename = (
        halt_wl.build_verification_filename(
            poll_time
        )
    )

    assert filename == (
        "2026-08-18-14-30-45-"
        "NASDAQ-HALT-abcdef01-WL.csv"
    )


def test_read_watchlist_symbols_handles_tos_preamble(
    tmp_path: Path,
) -> None:
    path = tmp_path / "watchlist.csv"

    path.write_text(
        "\ufeffWatchlist 'default'\n"
        "\n"
        "default\n"
        "Symbol,OV_DECISION,Last,Volume\n"
        "NVDA,100,220.00,\"1,000\"\n"
        "TSLA,50,330.00,\"2,000\"\n"
        "AAPL,25,310.00,\"3,000\"\n",
        encoding="utf-8",
    )

    symbols = (
        halt_wl.read_watchlist_symbols(
            path
        )
    )

    assert symbols == {
        "NVDA",
        "TSLA",
        "AAPL",
    }


def test_read_watchlist_symbols_requires_symbol_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.csv"

    path.write_text(
        "Watchlist 'default'\n"
        "\n"
        "default\n"
        "Ticker,Last\n"
        "NVDA,220\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="does not contain a Symbol header",
    ):
        halt_wl.read_watchlist_symbols(
            path
        )


def test_submit_and_verify_live_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    verification_dir = (
        tmp_path / "watchlist_verify"
    )

    target_filename = (
        "test-NASDAQ-HALT-WL.csv"
    )

    monkeypatch.setattr(
        halt_wl,
        "build_verification_filename",
        lambda poll_time: target_filename,
    )

    def fake_check_scanner_ready(
        *,
        root=None,
        allow_exports_suspended=False,
    ):
        del root

        if allow_exports_suspended:
            events.append(
                "check-suspended"
            )
        else:
            events.append(
                "check-active"
            )

        return SimpleNamespace(
            ready=True,
            detail="ready",
            status="HEALTHY",
        )

    monkeypatch.setattr(
        halt_wl,
        "check_scanner_ready",
        fake_check_scanner_ready,
    )

    def fake_control(
        *,
        action,
        root,
        wait,
    ):
        del root, wait

        events.append(action)

        return SimpleNamespace(
            successful=True,
            return_code=0,
        )

    monkeypatch.setattr(
        halt_wl,
        "run_scanner_control_command",
        fake_control,
    )

    submission_result = SimpleNamespace(
        submitted=True,
        successful=True,
        return_code=0,
        run_record_path=(
            tmp_path / "run.json"
        ),
    )

    def fake_submit(**kwargs):
        preflight_checker = (
            kwargs["preflight_checker"]
        )

        preflight = preflight_checker(
            root=kwargs["root"],
        )

        assert preflight.ready is True

        events.append(
            "add_wl_symbols"
        )

        return submission_result

    monkeypatch.setattr(
        halt_wl,
        "submit_watchlist_symbols",
        fake_submit,
    )

    def fake_export(
        *,
        target_filename,
        root,
        wait,
    ):
        del root, wait

        events.append(
            "export_wl"
        )

        verification_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        verification_path = (
            verification_dir
            / target_filename
        )

        verification_path.write_text(
            "\ufeffWatchlist 'default'\n"
            "\n"
            "default\n"
            "Symbol,Last\n"
            "NVDA,220\n"
            "TSLA,330\n",
            encoding="utf-8",
        )

        return (
            (
                "mb-scan-command",
                "export_wl",
            ),
            0,
        )

    monkeypatch.setattr(
        halt_wl,
        "run_verification_export",
        fake_export,
    )

    poll_time = datetime(
        2026,
        8,
        18,
        14,
        30,
        tzinfo=halt_wl.ET_ZONE,
    )

    result, verification_path = (
        halt_wl.submit_and_verify_live(
            symbols=[
                "NVDA",
                "TSLA",
            ],
            poll_time=poll_time,
            wait=30.0,
            root=None,
            output_dir=(
                tmp_path / "output"
            ),
            verification_dir=(
                verification_dir
            ),
        )
    )

    assert result is submission_result

    assert verification_path == (
        verification_dir
        / target_filename
    )

    assert events == [
        "check-active",
        "suspend_exports",
        "check-suspended",
        "check-suspended",
        "add_wl_symbols",
        "export_wl",
        "resume_exports",
        "check-active",
    ]


def test_verification_failure_still_resumes_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_actions: list[str] = []

    verification_dir = (
        tmp_path / "watchlist_verify"
    )

    target_filename = (
        "missing-symbol-WL.csv"
    )

    monkeypatch.setattr(
        halt_wl,
        "build_verification_filename",
        lambda poll_time: target_filename,
    )

    monkeypatch.setattr(
        halt_wl,
        "check_scanner_ready",
        lambda **kwargs: SimpleNamespace(
            ready=True,
            detail="ready",
            status="HEALTHY",
        ),
    )

    def fake_control(
        *,
        action,
        root,
        wait,
    ):
        del root, wait

        control_actions.append(
            action
        )

        return SimpleNamespace(
            successful=True,
            return_code=0,
        )

    monkeypatch.setattr(
        halt_wl,
        "run_scanner_control_command",
        fake_control,
    )

    monkeypatch.setattr(
        halt_wl,
        "submit_watchlist_symbols",
        lambda **kwargs: SimpleNamespace(
            submitted=True,
            successful=True,
            return_code=0,
            run_record_path=(
                tmp_path / "run.json"
            ),
        ),
    )

    def fake_export(
        *,
        target_filename,
        root,
        wait,
    ):
        del root, wait

        verification_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            verification_dir
            / target_filename
        ).write_text(
            "\ufeffWatchlist 'default'\n"
            "\n"
            "default\n"
            "Symbol,Last\n"
            "NVDA,220\n",
            encoding="utf-8",
        )

        return (
            ("mb-scan-command",),
            0,
        )

    monkeypatch.setattr(
        halt_wl,
        "run_verification_export",
        fake_export,
    )

    poll_time = datetime(
        2026,
        8,
        18,
        14,
        30,
        tzinfo=halt_wl.ET_ZONE,
    )

    with pytest.raises(
        RuntimeError,
        match="TSLA",
    ):
        halt_wl.submit_and_verify_live(
            symbols=[
                "NVDA",
                "TSLA",
            ],
            poll_time=poll_time,
            wait=30.0,
            root=None,
            output_dir=(
                tmp_path / "output"
            ),
            verification_dir=(
                verification_dir
            ),
        )

    assert control_actions == [
        "suspend_exports",
        "resume_exports",
    ]


def test_add_failure_still_resumes_and_does_not_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_actions: list[str] = []

    monkeypatch.setattr(
        halt_wl,
        "build_verification_filename",
        lambda poll_time: (
            "add-failure-WL.csv"
        ),
    )

    monkeypatch.setattr(
        halt_wl,
        "check_scanner_ready",
        lambda **kwargs: SimpleNamespace(
            ready=True,
            detail="ready",
            status="HEALTHY",
        ),
    )

    def fake_control(
        *,
        action,
        root,
        wait,
    ):
        del root, wait

        control_actions.append(
            action
        )

        return SimpleNamespace(
            successful=True,
            return_code=0,
        )

    monkeypatch.setattr(
        halt_wl,
        "run_scanner_control_command",
        fake_control,
    )

    monkeypatch.setattr(
        halt_wl,
        "submit_watchlist_symbols",
        lambda **kwargs: SimpleNamespace(
            submitted=True,
            successful=False,
            return_code=1,
            run_record_path=(
                tmp_path / "run.json"
            ),
        ),
    )

    def unexpected_export(**kwargs):
        raise AssertionError(
            "Verification export should not "
            "run after add failure."
        )

    monkeypatch.setattr(
        halt_wl,
        "run_verification_export",
        unexpected_export,
    )

    poll_time = datetime(
        2026,
        8,
        18,
        14,
        30,
        tzinfo=halt_wl.ET_ZONE,
    )

    with pytest.raises(
        RuntimeError,
        match="add_wl_symbols",
    ):
        halt_wl.submit_and_verify_live(
            symbols=["NVDA"],
            poll_time=poll_time,
            wait=30.0,
            root=None,
            output_dir=(
                tmp_path / "output"
            ),
            verification_dir=(
                tmp_path
                / "watchlist_verify"
            ),
        )

    assert control_actions == [
        "suspend_exports",
        "resume_exports",
    ]


def test_current_mode_first_poll_establishes_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeMonitor:
        def __init__(self) -> None:
            self.seen_symbols: set[str] = set()

        def pending_symbols(
            self,
            records,
            *,
            session_date,
        ):
            del session_date

            return [
                symbol
                for symbol in records
                if symbol
                not in self.seen_symbols
            ]

        def mark_seen(
            self,
            symbols,
            *,
            session_date,
        ) -> None:
            del session_date

            self.seen_symbols.update(
                symbols
            )

    feeds = iter(
        [
            SimpleNamespace(
                retrieval_mode="CURRENT",
                records=[
                    "OLD1",
                    "OLD2",
                ],
            ),
            SimpleNamespace(
                retrieval_mode="CURRENT",
                records=[
                    "OLD1",
                    "OLD2",
                    "NEW1",
                ],
            ),
        ]
    )

    def fake_fetch_trade_halts(
        *,
        halt_date,
        timeout,
    ):
        del timeout

        assert halt_date is None

        return next(feeds)

    submissions: list[list[str]] = []

    def fake_submit_watchlist_symbols(
        **kwargs,
    ):
        submissions.append(
            list(kwargs["symbols"])
        )

        return SimpleNamespace(
            run_record_path=(
                tmp_path / "run.json"
            ),
        )

    monkeypatch.setattr(
        halt_wl,
        "NasdaqHaltMonitor",
        FakeMonitor,
    )

    monkeypatch.setattr(
        halt_wl,
        "fetch_trade_halts",
        fake_fetch_trade_halts,
    )

    monkeypatch.setattr(
        halt_wl,
        "now_et",
        lambda: datetime(
            2026,
            8,
            18,
            14,
            30,
            tzinfo=halt_wl.ET_ZONE,
        ),
    )

    monkeypatch.setattr(
        halt_wl.time,
        "sleep",
        lambda seconds: None,
    )

    monkeypatch.setattr(
        halt_wl,
        "build_watchlist_command",
        lambda **kwargs: (
            "mb-scan-command",
            "add_wl_symbols",
        ),
    )

    monkeypatch.setattr(
        halt_wl,
        "submit_watchlist_symbols",
        fake_submit_watchlist_symbols,
    )

    monkeypatch.setattr(
        halt_wl.sys,
        "argv",
        [
            "run_nasdaq_halt_watchlist.py",
            "--polls",
            "2",
            "--interval",
            "60",
            "--output-dir",
            str(tmp_path),
        ],
    )

    result = halt_wl.main()

    assert result == 0

    # OLD1 and OLD2 existed when the controller
    # started and therefore form the baseline.
    # Only NEW1 from the second poll is processed.
    assert submissions == [
        ["NEW1"],
    ]

    output = (
        capsys.readouterr().out
    )

    assert (
        "Baseline     : 2 existing "
        "halt symbol(s)"
        in output
    )

    assert (
        "Action       : startup baseline "
        "only; no Watchlist submission"
        in output
    )
