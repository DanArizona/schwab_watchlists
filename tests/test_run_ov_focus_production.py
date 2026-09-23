from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import run_ov_focus_production as module


ET = ZoneInfo("America/New_York")


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


def test_decision_id_identifies_api_ov_source():
    observed = datetime(2026, 9, 21, 8, 25, 30, tzinfo=ET)

    decision_id = module.build_decision_id(observed)

    assert decision_id.startswith("api-ov-focus-20260921-082530-")
