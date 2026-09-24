"""Build opening Focus r1 from verified API-only OV evidence."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mb_market_data.opening_hierarchy import load_opening_proposal
from mb_market_data.sampling_membership import SamplingHierarchyRevision


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
API_OV_SOURCE_VERSION = "schwab-price-history-ov-v1"
API_OV_FOCUS_VERSION = "api-ov-focus-production-v1"
API_OV_FOCUS_SOURCE = "overnight-volume-api-v1"
SELECTION_POLICY = "OV_DECISION_DESC_SYMBOL_ASC"
EXPECTED_WINDOW_START = time(0, 0)
EXPECTED_WINDOW_END = time(9, 0)
EXPECTED_FREQUENCY_MINUTES = 5
OBSERVATION_FIELDS = (
    "symbol",
    "status",
    "ov_decision",
    "candle_count",
    "attempts",
    "window_start_et",
    "window_end_et",
    "request_started_at_utc",
    "response_received_at_utc",
    "http_status",
    "detail",
)


@dataclass(frozen=True, slots=True)
class APIOVObservation:
    """Validated API OV value used by Focus selection."""

    symbol: str
    ov_decision: int
    candle_count: int
    attempts: int
    http_status: int


@dataclass(frozen=True, slots=True)
class APIOVDecisionEvaluation:
    """Audit result for one symbol ranked by API OV."""

    symbol: str
    ov_rank: int
    eligible_rank: int
    eligible: bool
    selected: bool
    exclusion_reason: str | None


@dataclass(frozen=True, slots=True)
class APIOVSelection:
    """Deterministic API-OV top-N selection result."""

    selected_symbols: tuple[str, ...]
    eligible_count: int
    excluded_symbols: tuple[str, ...]
    evaluations: tuple[APIOVDecisionEvaluation, ...]


@dataclass(frozen=True, slots=True)
class APIOVEvidence:
    """Verified upstream API OV production bundle."""

    manifest_path: Path
    observations_path: Path
    candles_path: Path
    session_date: date
    completed_at_utc: datetime
    opening_content_sha256: str
    observations: tuple[APIOVObservation, ...]
    manifest: Mapping[str, Any]

    def by_symbol(self) -> dict[str, APIOVObservation]:
        return {item.symbol: item for item in self.observations}


@dataclass(frozen=True, slots=True)
class APIOVFocusArtifacts:
    """Immutable output paths from one API-OV Focus build."""

    root: Path
    decision_ledger: Path
    focus_symbols: Path
    proposal: Path
    manifest: Path
    revision: SamplingHierarchyRevision


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"JSON must contain one object: {path}")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"API OV manifest {name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"API OV manifest {name} must be nonblank text")
    return value.strip()


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"API OV manifest {name} must be an integer")
    if value < minimum:
        raise ValueError(
            f"API OV manifest {name} must be at least {minimum}"
        )
    return value


def _aware_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"API OV manifest {name} must be an ISO datetime")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"API OV manifest {name} must be an ISO datetime"
        ) from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"API OV manifest {name} must include an offset")
    return result


def _artifact_path(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    name: str,
) -> Path:
    artifacts = _mapping(manifest.get("artifacts"), "artifacts")
    record = _mapping(artifacts.get(name), f"artifacts.{name}")
    relative_text = _text(record.get("path"), f"artifacts.{name}.path")
    relative = Path(relative_text)
    if relative.is_absolute() or relative.name != relative_text:
        raise ValueError(
            f"API OV artifact {name} path must be a local filename"
        )
    path = manifest_path.parent / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = _text(record.get("sha256"), f"artifacts.{name}.sha256")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"API OV artifact hash differs for {name}")
    return path


def _parse_nonnegative_int(value: str, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"API OV observation {name} must be an integer"
        ) from error
    if result < 0:
        raise ValueError(
            f"API OV observation {name} must be nonnegative"
        )
    return result


def _read_observations(
    path: Path,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[APIOVObservation, ...]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != OBSERVATION_FIELDS:
            raise ValueError("API OV observations have unexpected columns")
        rows = list(reader)

    observations: list[APIOVObservation] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError(
                f"API OV observation row {row_number} has no symbol"
            )
        if symbol in seen:
            raise ValueError(f"API OV observations duplicate {symbol}")
        seen.add(symbol)
        if row.get("status") != "ok":
            raise ValueError(f"API OV observation for {symbol} is not OK")
        if row.get("detail"):
            raise ValueError(
                f"API OV observation for {symbol} has failure detail"
            )
        if row.get("window_start_et") != window_start.isoformat():
            raise ValueError(
                f"API OV observation for {symbol} has wrong window start"
            )
        if row.get("window_end_et") != window_end.isoformat():
            raise ValueError(
                f"API OV observation for {symbol} has wrong window end"
            )
        ov_decision = _parse_nonnegative_int(
            str(row.get("ov_decision") or ""), "ov_decision"
        )
        candle_count = _parse_nonnegative_int(
            str(row.get("candle_count") or ""), "candle_count"
        )
        attempts = _parse_nonnegative_int(
            str(row.get("attempts") or ""), "attempts"
        )
        if attempts < 1:
            raise ValueError(
                f"API OV observation for {symbol} has no request attempt"
            )
        http_status = _parse_nonnegative_int(
            str(row.get("http_status") or ""), "http_status"
        )
        if not 200 <= http_status < 300:
            raise ValueError(
                f"API OV observation for {symbol} has non-success HTTP status"
            )
        _aware_datetime(
            row.get("request_started_at_utc"),
            f"observation {symbol} request_started_at_utc",
        )
        _aware_datetime(
            row.get("response_received_at_utc"),
            f"observation {symbol} response_received_at_utc",
        )
        observations.append(
            APIOVObservation(
                symbol=symbol,
                ov_decision=ov_decision,
                candle_count=candle_count,
                attempts=attempts,
                http_status=http_status,
            )
        )
    return tuple(observations)


def load_api_ov_evidence(
    manifest_path: str | Path,
    *,
    opening_proposal_path: str | Path,
) -> tuple[APIOVEvidence, SamplingHierarchyRevision]:
    """Verify one production-eligible API OV bundle against opening r0."""

    source_manifest = Path(manifest_path).expanduser().resolve()
    opening_path = Path(opening_proposal_path).expanduser().resolve()
    if not source_manifest.is_file():
        raise FileNotFoundError(source_manifest)
    if not opening_path.is_file():
        raise FileNotFoundError(opening_path)
    manifest = _json_object(source_manifest)
    opening = load_opening_proposal(opening_path)
    if opening.revision != 0:
        raise ValueError("opening proposal must be revision r0")
    if opening.focus_symbols or opening.hot_symbols:
        raise ValueError("opening r0 must have empty Focus and Hot")

    if manifest.get("production_version") != API_OV_SOURCE_VERSION:
        raise ValueError("unsupported API OV production version")
    session_date = date.fromisoformat(
        _text(manifest.get("session_date"), "session_date")
    )
    if session_date != opening.session_date:
        raise ValueError("API OV session differs from opening session")
    opening_record = _mapping(manifest.get("opening"), "opening")
    if opening_record.get("sha256") != sha256_file(opening_path):
        raise ValueError("API OV opening proposal file hash differs")
    if opening_record.get("content_sha256") != opening.content_sha256:
        raise ValueError("API OV opening content hash differs")
    if opening_record.get("effective_at") != opening.effective_at.isoformat():
        raise ValueError("API OV opening effective time differs")

    expected_count = len(opening.uni_symbols)
    count_fields = (
        "opening_uni_count",
        "requested_symbols",
        "successful_symbols",
    )
    for name in count_fields:
        if _integer(manifest.get(name), name) != expected_count:
            raise ValueError(f"API OV {name} differs from opening Uni")
    if _integer(manifest.get("failed_symbols"), "failed_symbols") != 0:
        raise ValueError("API OV evidence contains failed symbols")
    if manifest.get("status_counts") != {"ok": expected_count}:
        raise ValueError("API OV status counts are not complete success")
    for name in (
        "complete_opening_uni",
        "completed_before_opening",
        "production_eligible",
    ):
        if manifest.get(name) is not True:
            raise ValueError(f"API OV manifest {name} is not true")
    if _integer(
        manifest.get("frequency_minutes"), "frequency_minutes"
    ) != EXPECTED_FREQUENCY_MINUTES:
        raise ValueError("API OV evidence has unexpected candle frequency")

    window_start = _aware_datetime(
        manifest.get("window_start_et"), "window_start_et"
    )
    window_end = _aware_datetime(
        manifest.get("window_end_et"), "window_end_et"
    )
    expected_start = datetime.combine(
        session_date, EXPECTED_WINDOW_START, tzinfo=ET
    )
    expected_end = datetime.combine(
        session_date, EXPECTED_WINDOW_END, tzinfo=ET
    )
    if window_start != expected_start or window_end != expected_end:
        raise ValueError("API OV evidence has unexpected decision window")
    started_at = _aware_datetime(
        manifest.get("started_at_utc"), "started_at_utc"
    ).astimezone(UTC)
    if started_at < expected_end.astimezone(UTC):
        raise ValueError(
            "API OV acquisition started before the decision window closed"
        )
    completed_at = _aware_datetime(
        manifest.get("completed_at_utc"), "completed_at_utc"
    ).astimezone(UTC)
    if completed_at >= opening.effective_at.astimezone(UTC):
        raise ValueError("API OV acquisition did not complete before opening")

    observations_path = _artifact_path(
        source_manifest, manifest, "api_ov_observations"
    )
    candles_path = _artifact_path(
        source_manifest, manifest, "api_ov_candles"
    )
    observations = _read_observations(
        observations_path,
        window_start=expected_start,
        window_end=expected_end,
    )
    observed_symbols = {item.symbol for item in observations}
    expected_symbols = set(opening.uni_symbols)
    if observed_symbols != expected_symbols:
        missing = sorted(expected_symbols - observed_symbols)
        extra = sorted(observed_symbols - expected_symbols)
        raise ValueError(
            "API OV observations do not exactly match opening Uni; "
            f"missing={missing}, extra={extra}"
        )

    return (
        APIOVEvidence(
            manifest_path=source_manifest,
            observations_path=observations_path,
            candles_path=candles_path,
            session_date=session_date,
            completed_at_utc=completed_at,
            opening_content_sha256=opening.content_sha256,
            observations=observations,
            manifest=manifest,
        ),
        opening,
    )


def select_api_ov_symbols(
    evidence: APIOVEvidence,
    *,
    limit: int,
) -> APIOVSelection:
    """Select deterministic top-N Focus membership from API OV values."""

    if limit <= 0:
        raise ValueError("OV selection limit must be positive")
    ranked = sorted(
        evidence.observations,
        key=lambda item: (-item.ov_decision, item.symbol),
    )
    selected_symbols = tuple(item.symbol for item in ranked[:limit])
    selected = set(selected_symbols)
    rank_by_symbol = {
        item.symbol: rank for rank, item in enumerate(ranked, start=1)
    }
    evaluations = tuple(
        APIOVDecisionEvaluation(
            symbol=item.symbol,
            ov_rank=rank_by_symbol[item.symbol],
            eligible_rank=rank_by_symbol[item.symbol],
            eligible=True,
            selected=item.symbol in selected,
            exclusion_reason=None,
        )
        for item in sorted(evidence.observations, key=lambda item: item.symbol)
    )
    return APIOVSelection(
        selected_symbols=selected_symbols,
        eligible_count=len(ranked),
        excluded_symbols=(),
        evaluations=evaluations,
    )


def _require_generation_time(
    opening: SamplingHierarchyRevision,
    generated_at: datetime,
) -> datetime:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    generated = generated_at.astimezone(ET)
    if generated.date() != opening.session_date:
        raise ValueError("Focus generation date differs from opening session")
    if generated >= opening.effective_at:
        raise ValueError("API OV Focus r1 must be generated before opening")
    return generated


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def hierarchy_proposal_payload(
    revision: SamplingHierarchyRevision,
) -> dict[str, Any]:
    """Return the strict schema-v2 publication proposal payload."""

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


def _write_decision_ledger(
    path: Path,
    evidence: APIOVEvidence,
    selection: APIOVSelection,
) -> None:
    observations = evidence.by_symbol()
    evaluations = {item.symbol: item for item in selection.evaluations}
    fields = (
        "symbol",
        "decision",
        "primary_reason",
        "ov_decision",
        "candle_count",
        "attempts",
        "ov_rank",
        "eligible_rank",
        "eligible",
        "selected",
    )
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for symbol in sorted(observations):
            observation = observations[symbol]
            evaluation = evaluations[symbol]
            writer.writerow(
                {
                    "symbol": symbol,
                    "decision": (
                        "include" if evaluation.selected else "exclude"
                    ),
                    "primary_reason": (
                        "included"
                        if evaluation.selected
                        else "below_selection_limit"
                    ),
                    "ov_decision": observation.ov_decision,
                    "candle_count": observation.candle_count,
                    "attempts": observation.attempts,
                    "ov_rank": evaluation.ov_rank,
                    "eligible_rank": evaluation.eligible_rank,
                    "eligible": "true",
                    "selected": str(evaluation.selected).lower(),
                }
            )


def _write_focus_symbols(
    path: Path,
    evidence: APIOVEvidence,
    selection: APIOVSelection,
) -> None:
    observations = evidence.by_symbol()
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output, fieldnames=("rank", "symbol", "ov_decision")
        )
        writer.writeheader()
        for rank, symbol in enumerate(selection.selected_symbols, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "symbol": symbol,
                    "ov_decision": observations[symbol].ov_decision,
                }
            )


def _build_revision(
    opening: SamplingHierarchyRevision,
    evidence: APIOVEvidence,
    selection: APIOVSelection,
    *,
    requested_limit: int,
    generated_at: datetime,
    opening_sha256: str,
    ledger_sha256: str,
    symbols_sha256: str,
) -> SamplingHierarchyRevision:
    metadata = {
        "producer": API_OV_FOCUS_SOURCE,
        "production_version": API_OV_FOCUS_VERSION,
        "generated_at_utc": generated_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "selection_policy": SELECTION_POLICY,
        "requested_limit": requested_limit,
        "eligible_count": selection.eligible_count,
        "ranked_focus_symbols": list(selection.selected_symbols),
        "opening_revision": opening.revision,
        "opening_content_sha256": opening.content_sha256,
        "opening_proposal_sha256": opening_sha256,
        "api_ov_manifest_sha256": sha256_file(evidence.manifest_path),
        "api_ov_observations_sha256": sha256_file(
            evidence.observations_path
        ),
        "api_ov_candles_sha256": sha256_file(evidence.candles_path),
        "decision_ledger_sha256": ledger_sha256,
        "focus_symbols_sha256": symbols_sha256,
    }
    return SamplingHierarchyRevision(
        session_date=opening.session_date,
        revision=1,
        effective_at=opening.effective_at,
        uni_symbols=opening.uni_symbols,
        focus_symbols=selection.selected_symbols,
        hot_symbols=(),
        source=API_OV_FOCUS_SOURCE,
        reason="opening Focus from API-only OV BASE_SET",
        metadata=metadata,
    )


def write_api_ov_focus_artifacts(
    output_dir: str | Path,
    *,
    opening_proposal_path: str | Path,
    evidence: APIOVEvidence,
    opening: SamplingHierarchyRevision,
    selection: APIOVSelection,
    requested_limit: int,
    generated_at: datetime,
    decision_id: str,
) -> APIOVFocusArtifacts:
    """Write one immutable API-OV-to-Focus r1 production bundle."""

    root = Path(output_dir)
    opening_path = Path(opening_proposal_path).expanduser().resolve()
    if root.exists():
        raise FileExistsError(f"output directory already exists: {root}")
    if not opening_path.is_file():
        raise FileNotFoundError(opening_path)
    loaded_opening = load_opening_proposal(opening_path)
    if loaded_opening.content_sha256 != opening.content_sha256:
        raise ValueError("opening proposal differs from supplied opening")
    generated = _require_generation_time(opening, generated_at)
    if evidence.session_date != opening.session_date:
        raise ValueError("API OV evidence differs from opening session")
    if evidence.opening_content_sha256 != opening.content_sha256:
        raise ValueError("API OV evidence differs from opening content")
    if requested_limit <= 0:
        raise ValueError("requested_limit must be positive")
    if not selection.selected_symbols:
        raise ValueError("API OV selection produced no Focus symbols")
    expected_selection = select_api_ov_symbols(
        evidence, limit=requested_limit
    )
    if selection != expected_selection:
        raise ValueError("API OV selection differs from deterministic top-N")
    if set(selection.selected_symbols) - set(opening.uni_symbols):
        raise ValueError("API OV Focus contains symbols outside opening Uni")

    root.mkdir(parents=True)
    ledger_path = root / "focus_decision_ledger.csv"
    symbols_path = root / "focus_symbols.csv"
    proposal_path = root / "sampling_hierarchy_r1.json"
    manifest_path = root / "manifest.json"
    _write_decision_ledger(ledger_path, evidence, selection)
    _write_focus_symbols(symbols_path, evidence, selection)
    revision = _build_revision(
        opening,
        evidence,
        selection,
        requested_limit=requested_limit,
        generated_at=generated,
        opening_sha256=sha256_file(opening_path),
        ledger_sha256=sha256_file(ledger_path),
        symbols_sha256=sha256_file(symbols_path),
    )
    _write_json(proposal_path, hierarchy_proposal_payload(revision))

    artifacts = {
        "focus_decision_ledger": ledger_path,
        "focus_symbols": symbols_path,
        "sampling_hierarchy_r1": proposal_path,
    }
    manifest = {
        "production_version": API_OV_FOCUS_VERSION,
        "decision_id": decision_id,
        "created_at_utc": generated.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "session_date": opening.session_date.isoformat(),
        "opening_revision": opening.revision,
        "output_revision": revision.revision,
        "effective_at": revision.effective_at.isoformat(),
        "selection_policy": SELECTION_POLICY,
        "requested_limit": requested_limit,
        "source_rows": len(evidence.observations),
        "uni_symbols": len(opening.uni_symbols),
        "eligible_symbols": selection.eligible_count,
        "selected_symbols": len(selection.selected_symbols),
        "content_sha256": revision.content_sha256,
        "inputs": {
            "opening_proposal": {
                "path": str(opening_path),
                "sha256": sha256_file(opening_path),
            },
            "api_ov_manifest": {
                "path": str(evidence.manifest_path),
                "sha256": sha256_file(evidence.manifest_path),
            },
            "api_ov_observations": {
                "path": str(evidence.observations_path),
                "sha256": sha256_file(evidence.observations_path),
            },
            "api_ov_candles": {
                "path": str(evidence.candles_path),
                "sha256": sha256_file(evidence.candles_path),
            },
        },
        "artifacts": {
            name: {"path": path.name, "sha256": sha256_file(path)}
            for name, path in artifacts.items()
        },
    }
    _write_json(manifest_path, manifest)
    return APIOVFocusArtifacts(
        root=root,
        decision_ledger=ledger_path,
        focus_symbols=symbols_path,
        proposal=proposal_path,
        manifest=manifest_path,
        revision=revision,
    )
