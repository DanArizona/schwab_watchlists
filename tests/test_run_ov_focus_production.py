from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import run_ov_focus_production as module


ET = ZoneInfo("America/New_York")


def test_resolve_source_date_uses_filename_date():
    assert module.resolve_source_date(
        Path("2026-09-21-08-20-00-WL.csv"),
        None,
    ) == date(2026, 9, 21)


def test_resolve_source_date_requires_date_for_undated_filename():
    with pytest.raises(ValueError, match="supply --watchlist-date"):
        module.resolve_source_date(Path("Watchlist.csv"), None)


def test_resolve_source_date_rejects_conflicting_explicit_date():
    with pytest.raises(ValueError, match="differs from filename"):
        module.resolve_source_date(
            Path("2026-09-20-Watchlist.csv"),
            date(2026, 9, 21),
        )


def test_default_output_dir_uses_eastern_timestamp():
    observed = datetime(2026, 9, 21, 8, 25, 30, tzinfo=ET)

    assert module.default_output_dir(
        Path("output") / "ov_focus_production",
        observed,
    ) == (
        Path("output")
        / "ov_focus_production"
        / "2026-09-21-08-25-30"
    )
