"""
Reusable output generation for candidate-pipeline results.

This module writes:

- the raw source response as JSON
- accepted symbols as a plain-text file
- pipeline settings, decisions, and source metadata as a run record

It contains no Schwab, command-line, GUI, or Watchlist submission logic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from candidate_pipeline import CandidatePipelineResult


_RESERVED_RUN_FIELDS = {
    "generated_at",
    "pipeline_source",
    "pipeline_evaluated_at",
    "input_count",
    "accepted_count",
    "rejected_count",
    "accepted_symbols",
    "filters",
    "rejections",
    "raw_response_file",
    "symbols_file",
}


@dataclass(frozen=True, slots=True)
class CandidateOutputPaths:
    """Paths created by one candidate-output operation."""

    raw_json: Path
    symbols_text: Path
    run_json: Path


def _validate_output_stem(stem: str) -> str:
    """Validate a filename stem without directory components."""

    normalized = stem.strip()

    if not normalized:
        raise ValueError("Output stem cannot be empty.")

    if Path(normalized).name != normalized:
        raise ValueError(
            "Output stem cannot contain directory components."
        )

    if "/" in normalized or "\\" in normalized:
        raise ValueError(
            "Output stem cannot contain path separators."
        )

    return normalized


def _filter_settings_record(
    result: CandidatePipelineResult,
) -> dict[str, Any]:
    """Convert pipeline filter settings into JSON-compatible data."""

    settings = result.settings

    return {
        "min_price": settings.min_price,
        "max_price": settings.max_price,
        "min_volume": settings.min_volume,
        "min_percent_change": settings.min_percent_change,
        "max_percent_change": settings.max_percent_change,
        "max_results": settings.max_results,
        "missing_field_policy": settings.missing_field_policy.value,
    }


def build_pipeline_run_record(
    result: CandidatePipelineResult,
    *,
    generated_at: datetime | None = None,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the JSON-compatible record for one pipeline run."""

    supplied_extra_fields = dict(extra_fields or {})

    collisions = sorted(
        _RESERVED_RUN_FIELDS.intersection(
            supplied_extra_fields
        )
    )

    if collisions:
        names = ", ".join(collisions)

        raise ValueError(
            f"Extra run fields use reserved names: {names}"
        )

    record: dict[str, Any] = {
        "generated_at": (
            generated_at or datetime.now().astimezone()
        ).isoformat(timespec="seconds"),
        "pipeline_source": result.source_name,
        "pipeline_evaluated_at": (
            result.evaluated_at.isoformat(timespec="seconds")
        ),
        "input_count": result.input_count,
        "accepted_count": result.accepted_count,
        "rejected_count": result.rejected_count,
        "accepted_symbols": list(result.accepted_symbols),
        "filters": _filter_settings_record(result),
        "rejections": [
            {
                "symbol": decision.candidate.symbol,
                "reasons": list(decision.reasons),
            }
            for decision in result.rejected
        ],
    }

    record.update(supplied_extra_fields)

    return record


def write_candidate_outputs(
    *,
    output_dir: Path,
    stem: str,
    raw_data: Any,
    pipeline_result: CandidatePipelineResult,
    extra_run_fields: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> CandidateOutputPaths:
    """Write raw data, accepted symbols, and the pipeline run record."""

    normalized_stem = _validate_output_stem(stem)
    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    paths = CandidateOutputPaths(
        raw_json=(
            resolved_output_dir
            / f"{normalized_stem}-raw.json"
        ),
        symbols_text=(
            resolved_output_dir
            / f"{normalized_stem}-symbols.txt"
        ),
        run_json=(
            resolved_output_dir
            / f"{normalized_stem}-run.json"
        ),
    )

    with paths.raw_json.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output_file:
        json.dump(
            raw_data,
            output_file,
            indent=2,
            ensure_ascii=False,
        )
        output_file.write("\n")

    symbols_text = "\n".join(
        pipeline_result.accepted_symbols
    )

    if symbols_text:
        symbols_text += "\n"

    paths.symbols_text.write_text(
        symbols_text,
        encoding="utf-8",
        newline="\n",
    )

    run_record = build_pipeline_run_record(
        pipeline_result,
        generated_at=generated_at,
        extra_fields=extra_run_fields,
    )

    run_record["raw_response_file"] = str(paths.raw_json)
    run_record["symbols_file"] = str(paths.symbols_text)

    with paths.run_json.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output_file:
        json.dump(
            run_record,
            output_file,
            indent=2,
            ensure_ascii=False,
        )
        output_file.write("\n")

    return paths
