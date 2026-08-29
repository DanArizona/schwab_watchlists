from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from schwab_watchlists.ov_coordinator import (
    OVDecisionEvaluation,
    OVSelection,
)
from schwab_watchlists.ov_decision_evidence import (
    write_ov_decision_evidence,
)


UTC = timezone.utc


@dataclass(frozen=True)
class FakeSnapshot:
    symbol: str
    raw_ov_decision: str
    ov_decision: Decimal
    quote_status: str


def test_write_ov_decision_evidence(tmp_path):
    source_path = (
        tmp_path / "COORD-OBS-test-WL.csv"
    )

    source_bytes = (
        b"Watchlist\n\nResults\n"
        b"Symbol,OV_DECISION\n"
        b"FNGR,2.799640564802E8\n"
        b"CYAB,2.9352016E7\n"
    )

    source_path.write_bytes(
        source_bytes
    )

    snapshots = (
        FakeSnapshot(
            symbol="FNGR",
            raw_ov_decision=(
                "2.799640564802E8"
            ),
            ov_decision=Decimal(
                "279964056.4802"
            ),
            quote_status="quote",
        ),
        FakeSnapshot(
            symbol="CYAB",
            raw_ov_decision=(
                "2.9352016E7"
            ),
            ov_decision=Decimal(
                "29352016"
            ),
            quote_status="quote",
        ),
    )

    batch = SimpleNamespace(
        trade_date=date(
            2026,
            8,
            28,
        ),
        tos_observed_at_utc=datetime(
            2026,
            8,
            28,
            13,
            59,
            21,
            tzinfo=UTC,
        ),
        snapshots=snapshots,
        quote_request_count=1,
        quote_batch_size=400,
        unexpected_quote_symbols=(),
        quote_results_not_in_watchlist=(),
    )

    selection = OVSelection(
        selected_symbols=(
            "FNGR",
        ),
        eligible_count=2,
        excluded_symbols=(),
        evaluations=(
            OVDecisionEvaluation(
                symbol="FNGR",
                ov_rank=1,
                eligible_rank=1,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            ),
            OVDecisionEvaluation(
                symbol="CYAB",
                ov_rank=2,
                eligible_rank=2,
                eligible=True,
                selected=False,
                exclusion_reason=None,
            ),
        ),
    )

    output_path = (
        tmp_path
        / "evidence"
        / "decision.jsonl"
    )

    result = write_ov_decision_evidence(
        output_path,
        intent_id="ov-test-001",
        batch=batch,
        selection=selection,
        source_watchlist_path=source_path,
        requested_limit=1,
    )

    assert result == output_path
    assert output_path.is_file()

    records = [
        json.loads(line)
        for line in output_path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert len(records) == 3

    header = records[0]

    assert header["record_type"] == "batch"
    assert header["schema_version"] == 1
    assert header["intent_id"] == "ov-test-001"
    assert header["trade_date"] == "2026-08-28"
    assert header["requested_limit"] == 1
    assert header["selected_symbols"] == [
        "FNGR",
    ]

    assert (
        header["source_watchlist_sha256"]
        == hashlib.sha256(
            source_bytes
        ).hexdigest()
    )

    fngr = records[1]

    assert fngr["record_type"] == "symbol"
    assert fngr["symbol"] == "FNGR"

    assert (
        fngr["snapshot"]["raw_ov_decision"]
        == "2.799640564802E8"
    )

    assert (
        fngr["snapshot"]["ov_decision"]
        == "279964056.4802"
    )

    assert (
        fngr["evaluation"]["ov_rank"]
        == 1
    )

    assert (
        fngr["evaluation"]["selected"]
        is True
    )


def test_write_ov_decision_evidence_requires_matching_symbols(
    tmp_path,
):
    source_path = tmp_path / "source.csv"
    source_path.write_text(
        "Symbol,OV_DECISION\nFNGR,1\n",
        encoding="utf-8",
    )

    batch = SimpleNamespace(
        trade_date=date(
            2026,
            8,
            28,
        ),
        tos_observed_at_utc=datetime(
            2026,
            8,
            28,
            14,
            0,
            tzinfo=UTC,
        ),
        snapshots=(
            FakeSnapshot(
                symbol="FNGR",
                raw_ov_decision="1",
                ov_decision=Decimal("1"),
                quote_status="quote",
            ),
        ),
        quote_request_count=1,
        quote_batch_size=400,
        unexpected_quote_symbols=(),
        quote_results_not_in_watchlist=(),
    )

    selection = OVSelection(
        selected_symbols=(),
        eligible_count=0,
        excluded_symbols=(),
        evaluations=(),
    )

    with pytest.raises(
        ValueError,
        match="exactly match",
    ):
        write_ov_decision_evidence(
            tmp_path / "decision.jsonl",
            batch=batch,
            intent_id="ov-test-001",
            selection=selection,
            source_watchlist_path=(
                source_path
            ),
            requested_limit=10,
        )
