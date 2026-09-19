"""Durable daily OV BASE_SET artifacts and schema-v2 Focus r1 proposal."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from mb_market_data.decision_batch import DecisionSnapshotBatch
from mb_market_data.sampling_membership import SamplingHierarchyRevision

from schwab_watchlists.ov_coordinator import OVSelection
from schwab_watchlists.ov_decision_evidence import (
    write_ov_decision_evidence,
)


OV_FOCUS_PRODUCTION_VERSION = "ov-focus-production-v1"
OV_FOCUS_SOURCE = "overnight-volume-tos-v1"

PROPOSAL_FIELDS = frozenset(
    {
        "session_date",
        "revision",
        "effective_at",
        "uni_symbols",
        "focus_symbols",
        "hot_symbols",
        "source",
        "reason",
        "metadata",
    }
)
REQUIRED_PROPOSAL_FIELDS = frozenset(
    {
        "session_date",
        "revision",
        "effective_at",
        "uni_symbols",
        "focus_symbols",
        "hot_symbols",
        "source",
    }
)


@dataclass(frozen=True, slots=True)
class OVFocusArtifacts:
    """Paths and membership produced by one daily OV Focus build."""

    root: Path
    evidence: Path
    decision_ledger: Path
    focus_symbols: Path
    proposal: Path
    manifest: Path
    revision: SamplingHierarchyRevision


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of one file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("hierarchy proposal JSON must contain one object")
    return payload


def load_hierarchy_proposal(
    path: str | Path,
) -> SamplingHierarchyRevision:
    """Load one strict mb_market_data hierarchy proposal."""

    proposal_path = Path(path)
    payload = _read_json_object(proposal_path)
    unexpected = set(payload) - PROPOSAL_FIELDS
    missing = REQUIRED_PROPOSAL_FIELDS - set(payload)
    if unexpected:
        raise ValueError(
            "hierarchy proposal contains unexpected fields: "
            + ", ".join(sorted(unexpected))
        )
    if missing:
        raise ValueError(
            "hierarchy proposal is missing required fields: "
            + ", ".join(sorted(missing))
        )

    effective_at = datetime.fromisoformat(payload["effective_at"])
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError("effective_at must include an explicit UTC offset")

    return SamplingHierarchyRevision(
        session_date=date.fromisoformat(payload["session_date"]),
        revision=payload["revision"],
        effective_at=effective_at,
        uni_symbols=payload["uni_symbols"],
        focus_symbols=payload["focus_symbols"],
        hot_symbols=payload["hot_symbols"],
        source=payload["source"],
        reason=payload.get("reason"),
        metadata=payload.get("metadata", {}),
    )


def validate_complete_uni_coverage(
    batch: DecisionSnapshotBatch,
    opening: SamplingHierarchyRevision,
) -> tuple[str, ...]:
    """Fail when the OV source omitted any opening-Uni symbol."""

    observed = {snapshot.symbol for snapshot in batch.snapshots}
    missing = tuple(sorted(set(opening.uni_symbols) - observed))
    if missing:
        preview = ", ".join(missing[:20])
        suffix = "" if len(missing) <= 20 else ", ..."
        raise ValueError(
            "OV source does not cover the complete opening Uni; "
            f"missing {len(missing)} symbol(s): {preview}{suffix}"
        )
    return tuple(sorted(observed - set(opening.uni_symbols)))


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def build_focus_revision(
    opening: SamplingHierarchyRevision,
    selection: OVSelection,
    *,
    requested_limit: int,
    generated_at: datetime,
    opening_proposal_sha256: str,
    source_watchlist_sha256: str,
    evidence_sha256: str,
    decision_ledger_sha256: str,
    focus_symbols_sha256: str,
) -> SamplingHierarchyRevision:
    """Build the first daily OV-derived Focus revision from opening r0."""

    generated = _require_aware(generated_at, "generated_at")
    if opening.revision != 0:
        raise ValueError("opening hierarchy must be revision r0")
    if opening.focus_symbols or opening.hot_symbols:
        raise ValueError("opening r0 must have empty Focus and Hot")
    if generated >= opening.effective_at:
        raise ValueError(
            "OV Focus r1 must be generated before its opening effective time"
        )
    if requested_limit <= 0:
        raise ValueError("requested_limit must be positive")
    if not selection.selected_symbols:
        raise ValueError("OV selection produced no Focus symbols")

    outside_uni = sorted(
        set(selection.selected_symbols) - set(opening.uni_symbols)
    )
    if outside_uni:
        raise ValueError(
            "OV Focus contains symbol(s) outside Uni: "
            + ", ".join(outside_uni)
        )

    metadata = {
        "producer": OV_FOCUS_SOURCE,
        "production_version": OV_FOCUS_PRODUCTION_VERSION,
        "generated_at_utc": generated.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "selection_policy": "OV_DECISION_DESC_SYMBOL_ASC",
        "requested_limit": requested_limit,
        "eligible_count": selection.eligible_count,
        "ranked_focus_symbols": list(selection.selected_symbols),
        "opening_revision": opening.revision,
        "opening_content_sha256": opening.content_sha256,
        "opening_proposal_sha256": opening_proposal_sha256,
        "source_watchlist_sha256": source_watchlist_sha256,
        "ov_evidence_sha256": evidence_sha256,
        "decision_ledger_sha256": decision_ledger_sha256,
        "focus_symbols_sha256": focus_symbols_sha256,
    }
    return SamplingHierarchyRevision(
        session_date=opening.session_date,
        revision=opening.revision + 1,
        effective_at=opening.effective_at,
        uni_symbols=opening.uni_symbols,
        focus_symbols=selection.selected_symbols,
        hot_symbols=(),
        source=OV_FOCUS_SOURCE,
        reason="opening Focus from OV BASE_SET",
        metadata=metadata,
    )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def hierarchy_proposal_payload(
    revision: SamplingHierarchyRevision,
) -> dict[str, Any]:
    """Return the strict JSON payload accepted by the hierarchy publisher."""

    return {
        "session_date": revision.session_date.isoformat(),
        "revision": revision.revision,
        "effective_at": revision.effective_at.isoformat(),
        "uni_symbols": list(revision.uni_symbols),
        "focus_symbols": list(revision.focus_symbols),
        "hot_symbols": list(revision.hot_symbols),
        "source": revision.source,
        "reason": revision.reason,
        "metadata": _plain_json(revision.metadata),
    }


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")


def _decimal_text(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _write_decision_ledger(
    path: Path,
    batch: DecisionSnapshotBatch,
    selection: OVSelection,
) -> None:
    evaluation_by_symbol = {
        evaluation.symbol: evaluation
        for evaluation in selection.evaluations
    }
    fields = [
        "symbol",
        "decision",
        "primary_reason",
        "ov_decision",
        "ov_decision_status",
        "quote_status",
        "ov_rank",
        "eligible_rank",
        "eligible",
        "selected",
    ]
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for snapshot in sorted(batch.snapshots, key=lambda item: item.symbol):
            evaluation = evaluation_by_symbol[snapshot.symbol]
            if evaluation.selected:
                reason = "included"
            elif evaluation.exclusion_reason is not None:
                reason = evaluation.exclusion_reason
            else:
                reason = "below_selection_limit"
            writer.writerow(
                {
                    "symbol": snapshot.symbol,
                    "decision": (
                        "include" if evaluation.selected else "exclude"
                    ),
                    "primary_reason": reason,
                    "ov_decision": _decimal_text(snapshot.ov_decision),
                    "ov_decision_status": snapshot.ov_decision_status.value,
                    "quote_status": snapshot.quote_status.value,
                    "ov_rank": evaluation.ov_rank or "",
                    "eligible_rank": evaluation.eligible_rank or "",
                    "eligible": str(evaluation.eligible).lower(),
                    "selected": str(evaluation.selected).lower(),
                }
            )


def _write_focus_symbols(
    path: Path,
    batch: DecisionSnapshotBatch,
    selection: OVSelection,
) -> None:
    snapshots = batch.by_symbol()
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=["rank", "symbol", "ov_decision"],
        )
        writer.writeheader()
        for rank, symbol in enumerate(selection.selected_symbols, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "symbol": symbol,
                    "ov_decision": _decimal_text(
                        snapshots[symbol].ov_decision
                    ),
                }
            )


def write_ov_focus_artifacts(
    output_dir: str | Path,
    *,
    opening_proposal_path: str | Path,
    source_watchlist_path: str | Path,
    batch: DecisionSnapshotBatch,
    selection: OVSelection,
    requested_limit: int,
    generated_at: datetime,
    decision_id: str,
) -> OVFocusArtifacts:
    """Write one immutable, fully hashed OV-to-Focus production bundle."""

    root = Path(output_dir)
    opening_path = Path(opening_proposal_path).resolve()
    watchlist_path = Path(source_watchlist_path).resolve()
    if root.exists():
        raise FileExistsError(f"output directory already exists: {root}")
    if not opening_path.is_file():
        raise FileNotFoundError(opening_path)
    if not watchlist_path.is_file():
        raise FileNotFoundError(watchlist_path)

    opening = load_hierarchy_proposal(opening_path)
    if batch.trade_date != opening.session_date:
        raise ValueError(
            "DecisionSnapshotBatch trade date differs from opening session"
        )
    extra_source_symbols = validate_complete_uni_coverage(batch, opening)

    root.mkdir(parents=True)
    evidence_path = root / "ov_decision_evidence.jsonl"
    ledger_path = root / "focus_decision_ledger.csv"
    symbols_path = root / "focus_symbols.csv"
    proposal_path = root / "sampling_hierarchy_r1.json"
    manifest_path = root / "manifest.json"

    write_ov_decision_evidence(
        evidence_path,
        intent_id=decision_id,
        batch=batch,
        selection=selection,
        source_watchlist_path=watchlist_path,
        requested_limit=requested_limit,
    )
    _write_decision_ledger(ledger_path, batch, selection)
    _write_focus_symbols(symbols_path, batch, selection)

    revision = build_focus_revision(
        opening,
        selection,
        requested_limit=requested_limit,
        generated_at=generated_at,
        opening_proposal_sha256=sha256_file(opening_path),
        source_watchlist_sha256=sha256_file(watchlist_path),
        evidence_sha256=sha256_file(evidence_path),
        decision_ledger_sha256=sha256_file(ledger_path),
        focus_symbols_sha256=sha256_file(symbols_path),
    )
    _write_json_exclusive(
        proposal_path,
        hierarchy_proposal_payload(revision),
    )

    artifact_paths = {
        "ov_decision_evidence": evidence_path,
        "focus_decision_ledger": ledger_path,
        "focus_symbols": symbols_path,
        "sampling_hierarchy_r1": proposal_path,
    }
    generated = _require_aware(generated_at, "generated_at")
    manifest = {
        "production_version": OV_FOCUS_PRODUCTION_VERSION,
        "decision_id": decision_id,
        "created_at_utc": generated.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "session_date": opening.session_date.isoformat(),
        "opening_revision": opening.revision,
        "output_revision": revision.revision,
        "effective_at": revision.effective_at.isoformat(),
        "selection_policy": "OV_DECISION_DESC_SYMBOL_ASC",
        "requested_limit": requested_limit,
        "source_rows": len(batch.snapshots),
        "uni_symbols": len(opening.uni_symbols),
        "eligible_symbols": selection.eligible_count,
        "selected_symbols": len(selection.selected_symbols),
        "extra_source_symbols": list(extra_source_symbols),
        "content_sha256": revision.content_sha256,
        "inputs": {
            "opening_proposal": {
                "path": str(opening_path),
                "sha256": sha256_file(opening_path),
            },
            "source_watchlist": {
                "path": str(watchlist_path),
                "sha256": sha256_file(watchlist_path),
            },
        },
        "artifacts": {
            name: {
                "path": path.name,
                "sha256": sha256_file(path),
            }
            for name, path in artifact_paths.items()
        },
    }
    _write_json_exclusive(manifest_path, manifest)

    return OVFocusArtifacts(
        root=root,
        evidence=evidence_path,
        decision_ledger=ledger_path,
        focus_symbols=symbols_path,
        proposal=proposal_path,
        manifest=manifest_path,
        revision=revision,
    )
