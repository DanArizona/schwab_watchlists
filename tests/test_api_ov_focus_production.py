from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mb_market_data.api_overnight_volume import (
    ET,
    APIOvernightVolumeBatch,
    APIOvernightVolumeCandle,
    APIOvernightVolumeObservation,
    APIOvernightVolumeStatus,
    write_api_overnight_volume_artifacts,
)
from mb_market_data.opening_hierarchy import (
    opening_proposal_payload,
)
from mb_market_data.quote_observation_store import (
    HIERARCHY_SCHEMA_VERSION,
    QuoteObservationStore,
    RecordResult,
)
from mb_market_data.sampling_membership import SamplingHierarchyRevision

from schwab_watchlists.api_ov_focus_production import (
    load_api_ov_evidence,
    select_api_ov_symbols,
    sha256_file,
    write_api_ov_focus_artifacts,
)
from schwab_watchlists.ov_focus_production import load_hierarchy_proposal


UTC = timezone.utc
SESSION_DATE = date(2026, 9, 21)


def opening_revision() -> SamplingHierarchyRevision:
    return SamplingHierarchyRevision(
        session_date=SESSION_DATE,
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
    opening = opening_revision()
    path.write_text(
        json.dumps(opening_proposal_payload(opening), indent=2) + "\n",
        encoding="utf-8",
    )
    return opening


def observation(symbol: str, volume: int) -> APIOvernightVolumeObservation:
    start = datetime(2026, 9, 21, 12, 25, tzinfo=UTC)
    candle = APIOvernightVolumeCandle(
        start_et=datetime(2026, 9, 21, 7, 0, tzinfo=ET),
        open=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        volume=volume,
    )
    return APIOvernightVolumeObservation(
        symbol=symbol,
        trade_date=SESSION_DATE,
        window_start_et=datetime(2026, 9, 21, 0, 0, tzinfo=ET),
        window_end_et=datetime(2026, 9, 21, 8, 25, tzinfo=ET),
        status=APIOvernightVolumeStatus.OK,
        ov_decision=volume,
        candle_count=1,
        attempts=1,
        request_started_at_utc=start,
        response_received_at_utc=start + timedelta(seconds=1),
        http_status=200,
        detail=None,
        candles=(candle,),
    )


def write_source_bundle(
    root: Path,
    opening_path: Path,
    opening: SamplingHierarchyRevision,
    *,
    complete: bool = True,
) -> Path:
    start = datetime(2026, 9, 21, 12, 25, tzinfo=UTC)
    observations = (
        observation("AAAA", 100),
        observation("BBBB", 300),
        observation("CCCC", 200),
    )
    batch = APIOvernightVolumeBatch(
        trade_date=SESSION_DATE,
        window_start_et=observations[0].window_start_et,
        window_end_et=observations[0].window_end_et,
        started_at_utc=start,
        completed_at_utc=start + timedelta(minutes=1),
        request_interval_seconds=0.5,
        max_attempts=3,
        observations=observations,
    )
    artifacts = write_api_overnight_volume_artifacts(
        root,
        opening_proposal_path=opening_path,
        opening_content_sha256=opening.content_sha256,
        opening_uni_count=len(opening.uni_symbols),
        opening_effective_at=opening.effective_at,
        complete_opening_uni=complete,
        batch=batch,
    )
    return artifacts.manifest


def test_loads_complete_hashed_api_evidence_and_selects_top_n(tmp_path):
    opening_path = tmp_path / "opening.json"
    opening = write_opening(opening_path)
    source_manifest = write_source_bundle(
        tmp_path / "api-ov", opening_path, opening
    )

    evidence, loaded_opening = load_api_ov_evidence(
        source_manifest,
        opening_proposal_path=opening_path,
    )
    selection = select_api_ov_symbols(evidence, limit=2)

    assert loaded_opening.content_sha256 == opening.content_sha256
    assert selection.selected_symbols == ("BBBB", "CCCC")
    assert selection.eligible_count == 3
    assert selection.excluded_symbols == ()


def test_rejects_tampered_observation_artifact(tmp_path):
    opening_path = tmp_path / "opening.json"
    opening = write_opening(opening_path)
    source_manifest = write_source_bundle(
        tmp_path / "api-ov", opening_path, opening
    )
    observations = source_manifest.parent / "api_ov_observations.csv"
    observations.write_text(
        observations.read_text(encoding="utf-8") + "EXTRA\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="artifact hash differs"):
        load_api_ov_evidence(
            source_manifest,
            opening_proposal_path=opening_path,
        )


def test_rejects_bundle_not_marked_complete_for_opening_uni(tmp_path):
    opening_path = tmp_path / "opening.json"
    opening = write_opening(opening_path)
    source_manifest = write_source_bundle(
        tmp_path / "api-ov",
        opening_path,
        opening,
        complete=False,
    )

    with pytest.raises(ValueError, match="complete_opening_uni"):
        load_api_ov_evidence(
            source_manifest,
            opening_proposal_path=opening_path,
        )


def test_writes_publishable_api_only_focus_r1(tmp_path):
    opening_path = tmp_path / "opening.json"
    opening = write_opening(opening_path)
    source_manifest = write_source_bundle(
        tmp_path / "api-ov", opening_path, opening
    )
    evidence, loaded_opening = load_api_ov_evidence(
        source_manifest,
        opening_proposal_path=opening_path,
    )
    selection = select_api_ov_symbols(evidence, limit=2)

    artifacts = write_api_ov_focus_artifacts(
        tmp_path / "focus",
        opening_proposal_path=opening_path,
        evidence=evidence,
        opening=loaded_opening,
        selection=selection,
        requested_limit=2,
        generated_at=datetime(2026, 9, 21, 8, 27, tzinfo=ET),
        decision_id="api-ov-focus-test",
    )

    assert artifacts.revision.revision == 1
    assert artifacts.revision.source == "overnight-volume-api-v1"
    assert artifacts.revision.focus_symbols == ("BBBB", "CCCC")
    proposal = load_hierarchy_proposal(artifacts.proposal)
    assert proposal.content_sha256 == artifacts.revision.content_sha256
    with artifacts.decision_ledger.open(
        encoding="utf-8", newline=""
    ) as source:
        ledger = {row["symbol"]: row for row in csv.DictReader(source)}
    assert ledger["BBBB"]["decision"] == "include"
    assert ledger["AAAA"]["primary_reason"] == "below_selection_limit"
    manifest = json.loads(artifacts.manifest.read_text(encoding="utf-8"))
    assert "source_watchlist" not in manifest["inputs"]
    assert (
        manifest["inputs"]["api_ov_manifest"]["sha256"]
        == sha256_file(source_manifest)
    )

    database = tmp_path / "2026-09-21.sqlite3"
    store = QuoteObservationStore(
        database,
        session_date=opening.session_date,
        schema_version=HIERARCHY_SCHEMA_VERSION,
    )
    store.initialize()
    publication_time = datetime(2026, 9, 21, 13, 29, tzinfo=UTC)
    with patch(
        "mb_market_data.quote_observation_store._utc_now",
        return_value=publication_time,
    ):
        assert store.record_membership_revision(opening) is RecordResult.INSERTED
        assert store.record_membership_revision(proposal) is RecordResult.INSERTED


def test_rejects_late_focus_generation_without_creating_output(tmp_path):
    opening_path = tmp_path / "opening.json"
    opening = write_opening(opening_path)
    source_manifest = write_source_bundle(
        tmp_path / "api-ov", opening_path, opening
    )
    evidence, loaded_opening = load_api_ov_evidence(
        source_manifest,
        opening_proposal_path=opening_path,
    )
    selection = select_api_ov_symbols(evidence, limit=2)
    output = tmp_path / "focus"

    with pytest.raises(ValueError, match="before opening"):
        write_api_ov_focus_artifacts(
            output,
            opening_proposal_path=opening_path,
            evidence=evidence,
            opening=loaded_opening,
            selection=selection,
            requested_limit=2,
            generated_at=datetime(2026, 9, 21, 9, 30, tzinfo=ET),
            decision_id="late",
        )
    assert not output.exists()
