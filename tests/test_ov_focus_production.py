from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from mb_market_data.quote_observation_store import (
    HIERARCHY_SCHEMA_VERSION,
    QuoteObservationStore,
    RecordResult,
)
from mb_market_data.sampling_membership import SamplingHierarchyRevision
from mb_market_data.schwab_quotes import QuoteStatus
from mb_market_data.tos_watchlist import OVDecisionStatus

from schwab_watchlists.ov_coordinator import select_ov_symbols
from schwab_watchlists.ov_focus_production import (
    hierarchy_proposal_payload,
    load_hierarchy_proposal,
    sha256_file,
    validate_complete_uni_coverage,
    write_ov_focus_artifacts,
)


ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True)
class FakeSnapshot:
    symbol: str
    ov_decision: Decimal | None
    ov_decision_status: OVDecisionStatus
    quote_status: QuoteStatus
    raw_ov_decision: str = ""

    @property
    def has_usable_ov_decision(self) -> bool:
        return self.ov_decision_status in {
            OVDecisionStatus.NUMERIC,
            OVDecisionStatus.ZERO,
        }

    @property
    def has_schwab_quote(self) -> bool:
        return self.quote_status is QuoteStatus.QUOTE


class FakeBatch(SimpleNamespace):
    def by_symbol(self):
        return {snapshot.symbol: snapshot for snapshot in self.snapshots}


def snapshot(
    symbol: str,
    ov: str | None,
    *,
    ov_status: OVDecisionStatus = OVDecisionStatus.NUMERIC,
    quote_status: QuoteStatus = QuoteStatus.QUOTE,
) -> FakeSnapshot:
    return FakeSnapshot(
        symbol=symbol,
        ov_decision=None if ov is None else Decimal(ov),
        ov_decision_status=ov_status,
        quote_status=quote_status,
        raw_ov_decision="" if ov is None else ov,
    )


def batch(*snapshots: FakeSnapshot) -> FakeBatch:
    return FakeBatch(
        trade_date=date(2026, 9, 21),
        tos_observed_at_utc=datetime(2026, 9, 21, 12, 20, tzinfo=UTC),
        snapshots=tuple(snapshots),
        quote_request_count=1,
        quote_batch_size=400,
        unexpected_quote_symbols=(),
        quote_results_not_in_watchlist=(),
    )


def opening_revision() -> SamplingHierarchyRevision:
    return SamplingHierarchyRevision(
        session_date=date(2026, 9, 21),
        revision=0,
        effective_at=datetime(2026, 9, 21, 9, 30, tzinfo=ET),
        uni_symbols=("AAAA", "BBBB", "CCCC"),
        focus_symbols=(),
        hot_symbols=(),
        source="daily-universe-production-v1",
        reason="opening Uni",
        metadata={"test": True},
    )


def write_opening(path: Path) -> SamplingHierarchyRevision:
    revision = opening_revision()
    path.write_text(
        json.dumps(hierarchy_proposal_payload(revision), indent=2) + "\n",
        encoding="utf-8",
    )
    return revision


def write_source(path: Path) -> None:
    path.write_text(
        "Symbol,OV_DECISION\n"
        "AAAA,100\n"
        "BBBB,300\n"
        "CCCC,200\n"
        "OUT,1000\n",
        encoding="utf-8",
    )


def test_complete_uni_coverage_allows_extra_source_rows():
    source = batch(
        snapshot("AAAA", "100"),
        snapshot("BBBB", "300"),
        snapshot("CCCC", "200"),
        snapshot("OUT", "1000"),
    )

    assert validate_complete_uni_coverage(
        source,
        opening_revision(),
    ) == ("OUT",)


def test_complete_uni_coverage_rejects_missing_symbols():
    source = batch(snapshot("AAAA", "100"), snapshot("BBBB", "300"))

    with pytest.raises(ValueError, match="missing 1 symbol.*CCCC"):
        validate_complete_uni_coverage(source, opening_revision())


def test_writes_publishable_r1_and_durable_decision_artifacts(tmp_path):
    opening_path = tmp_path / "opening_hierarchy_r0.json"
    source_path = tmp_path / "2026-09-21-08-20-00-WL.csv"
    r0 = write_opening(opening_path)
    write_source(source_path)
    source = batch(
        snapshot("AAAA", "100"),
        snapshot("BBBB", "300"),
        snapshot("CCCC", "200"),
        snapshot("OUT", "1000"),
    )
    selection = select_ov_symbols(
        source,
        limit=2,
        allowed_symbols=r0.uni_symbols,
    )

    artifacts = write_ov_focus_artifacts(
        tmp_path / "result",
        opening_proposal_path=opening_path,
        source_watchlist_path=source_path,
        batch=source,
        selection=selection,
        requested_limit=2,
        generated_at=datetime(2026, 9, 21, 8, 25, tzinfo=ET),
        decision_id="ov-focus-test",
    )

    assert selection.selected_symbols == ("BBBB", "CCCC")
    assert artifacts.revision.revision == 1
    assert artifacts.revision.uni_symbols == r0.uni_symbols
    assert artifacts.revision.focus_symbols == ("BBBB", "CCCC")
    assert artifacts.revision.hot_symbols == ()
    loaded = load_hierarchy_proposal(artifacts.proposal)
    assert loaded.content_sha256 == artifacts.revision.content_sha256

    with artifacts.decision_ledger.open(encoding="utf-8", newline="") as f:
        ledger = {row["symbol"]: row for row in csv.DictReader(f)}
    assert ledger["BBBB"]["decision"] == "include"
    assert ledger["AAAA"]["primary_reason"] == "below_selection_limit"
    assert ledger["OUT"]["primary_reason"] == "outside_uni"

    manifest = json.loads(artifacts.manifest.read_text(encoding="utf-8"))
    assert manifest["uni_symbols"] == 3
    assert manifest["selected_symbols"] == 2
    assert manifest["extra_source_symbols"] == ["OUT"]
    assert (
        manifest["artifacts"]["sampling_hierarchy_r1"]["sha256"]
        == sha256_file(artifacts.proposal)
    )

    database = tmp_path / "2026-09-21.sqlite3"
    store = QuoteObservationStore(
        database,
        session_date=r0.session_date,
        schema_version=HIERARCHY_SCHEMA_VERSION,
    )
    store.initialize()
    publication_time = datetime(2026, 9, 21, 13, 29, tzinfo=UTC)
    with patch(
        "mb_market_data.quote_observation_store._utc_now",
        return_value=publication_time,
    ):
        assert store.record_membership_revision(r0) is RecordResult.INSERTED
        assert (
            store.record_membership_revision(loaded)
            is RecordResult.INSERTED
        )
    revisions = store.membership_revisions_in_effective_order()
    assert [revision.revision for revision in revisions] == [0, 1]
    effective = store.latest_membership_revision_effective_at(
        r0.effective_at
    )
    assert effective is not None
    assert effective.revision == 1
    assert effective.focus_symbols == ("BBBB", "CCCC")


def test_rejects_late_generation_before_creating_output(tmp_path):
    opening_path = tmp_path / "opening_hierarchy_r0.json"
    source_path = tmp_path / "2026-09-21-WL.csv"
    r0 = write_opening(opening_path)
    write_source(source_path)
    source = batch(
        snapshot("AAAA", "100"),
        snapshot("BBBB", "300"),
        snapshot("CCCC", "200"),
    )
    selection = select_ov_symbols(
        source,
        limit=2,
        allowed_symbols=r0.uni_symbols,
    )
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="before.*effective"):
        write_ov_focus_artifacts(
            output,
            opening_proposal_path=opening_path,
            source_watchlist_path=source_path,
            batch=source,
            selection=selection,
            requested_limit=2,
            generated_at=datetime(2026, 9, 21, 9, 30, tzinfo=ET),
            decision_id="late",
        )
    assert not output.exists()


def test_rejects_empty_focus_before_creating_output(tmp_path):
    opening_path = tmp_path / "opening_hierarchy_r0.json"
    source_path = tmp_path / "2026-09-21-WL.csv"
    r0 = write_opening(opening_path)
    write_source(source_path)
    source = batch(
        snapshot(
            "AAAA",
            None,
            ov_status=OVDecisionStatus.BLANK,
        ),
        snapshot(
            "BBBB",
            None,
            ov_status=OVDecisionStatus.BLANK,
        ),
        snapshot(
            "CCCC",
            None,
            ov_status=OVDecisionStatus.BLANK,
        ),
    )
    selection = select_ov_symbols(
        source,
        limit=2,
        allowed_symbols=r0.uni_symbols,
    )
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="no Focus symbols"):
        write_ov_focus_artifacts(
            output,
            opening_proposal_path=opening_path,
            source_watchlist_path=source_path,
            batch=source,
            selection=selection,
            requested_limit=2,
            generated_at=datetime(2026, 9, 21, 8, 25, tzinfo=ET),
            decision_id="empty",
        )
    assert not output.exists()
