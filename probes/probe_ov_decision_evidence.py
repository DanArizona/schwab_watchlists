from __future__ import annotations

import getpass

from datetime import date
from pathlib import Path

from mb_tools.schwab_secure.client import (
    make_secure_schwab_client,
)

from schwab_watchlists.ov_coordinator import (
    acquire_live_ov_batch,
    select_ov_symbols,
)
from schwab_watchlists.ov_decision_evidence import (
    write_ov_decision_evidence,
)


SOURCE = Path(
    r"C:\Users\danla\Documents\github\thousand_miles"
    r"\SCANS\watchlist_verify\COORD-OBS-0faaa30e-WL.csv"
)

ECFG = Path(
    r"C:\Users\danla\MBV\secure_schwabdev.ecfg"
)

OUTPUT = Path(
    r"output\ov_decisions"
    r"\2026-08-28-after-hours-probe-OV-DECISION.jsonl"
)


def main() -> None:
    password = getpass.getpass(
        "ecfg password: "
    )

    client = make_secure_schwab_client(
        ECFG,
        password,
        timeout=10,
    )

    try:
        batch = acquire_live_ov_batch(
            client,
            SOURCE,
            trade_date=date(
                2026,
                8,
                28,
            ),
        )
    finally:
        client.close()

    selection = select_ov_symbols(
        batch,
        limit=10,
    )

    write_ov_decision_evidence(
        OUTPUT,
        intent_id=(
            "ov-after-hours-probe-20260828"
        ),
        batch=batch,
        selection=selection,
        source_watchlist_path=SOURCE,
        requested_limit=10,
    )

    print(
        "Selected : "
        + " ".join(
            selection.selected_symbols
        )
    )
    print(
        f"Eligible : "
        f"{selection.eligible_count}"
    )
    print(
        f"Evidence : "
        f"{OUTPUT.resolve()}"
    )


if __name__ == "__main__":
    main()
