import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from candidate_filters import FilterSettings
from candidate_model import SymbolCandidate
from candidate_outputs import (
    build_pipeline_run_record,
    write_candidate_outputs,
)
from candidate_pipeline import (
    CandidatePipelineResult,
    run_candidate_pipeline,
)


EVALUATED_AT = datetime(
    2026,
    8,
    2,
    18,
    0,
    tzinfo=timezone.utc,
)

GENERATED_AT = datetime(
    2026,
    8,
    2,
    18,
    1,
    tzinfo=timezone.utc,
)


def make_pipeline_result() -> CandidatePipelineResult:
    candidates = [
        SymbolCandidate(
            symbol="PASS",
            source="test_source",
            last_price=10.0,
            volume=1_000_000,
            percent_change=5.0,
        ),
        SymbolCandidate(
            symbol="FAIL",
            source="test_source",
            last_price=0.25,
            volume=100,
            percent_change=-5.0,
        ),
    ]

    return run_candidate_pipeline(
        candidates,
        FilterSettings(
            min_price=1.0,
            min_volume=1_000,
        ),
        source_name="test_source",
        evaluated_at=EVALUATED_AT,
    )


def test_build_pipeline_run_record() -> None:
    result = make_pipeline_result()

    record = build_pipeline_run_record(
        result,
        generated_at=GENERATED_AT,
        extra_fields={
            "market": "NASDAQ",
            "watchlist_action": None,
        },
    )

    assert record["generated_at"] == (
        "2026-08-02T18:01:00+00:00"
    )
    assert record["pipeline_source"] == "test_source"
    assert record["input_count"] == 2
    assert record["accepted_count"] == 1
    assert record["rejected_count"] == 1
    assert record["accepted_symbols"] == ["PASS"]

    assert record["filters"]["min_price"] == 1.0
    assert record["filters"]["min_volume"] == 1_000

    assert record["rejections"] == [
        {
            "symbol": "FAIL",
            "reasons": [
                "price 0.25 is below minimum 1",
                "volume 100 is below minimum 1,000",
            ],
        }
    ]

    assert record["market"] == "NASDAQ"
    assert record["watchlist_action"] is None


def test_write_candidate_outputs(tmp_path: Path) -> None:
    result = make_pipeline_result()
    raw_data = {
        "screeners": [
            {
                "symbol": "PASS",
            }
        ]
    }

    paths = write_candidate_outputs(
        output_dir=tmp_path,
        stem="test-movers",
        raw_data=raw_data,
        pipeline_result=result,
        generated_at=GENERATED_AT,
        extra_run_fields={
            "market": "NASDAQ",
        },
    )

    assert paths.raw_json.name == "test-movers-raw.json"
    assert paths.symbols_text.name == (
        "test-movers-symbols.txt"
    )
    assert paths.run_json.name == "test-movers-run.json"

    assert json.loads(
        paths.raw_json.read_text(encoding="utf-8")
    ) == raw_data

    assert paths.symbols_text.read_text(
        encoding="utf-8"
    ) == "PASS\n"

    run_record = json.loads(
        paths.run_json.read_text(encoding="utf-8")
    )

    assert run_record["accepted_symbols"] == ["PASS"]
    assert run_record["market"] == "NASDAQ"
    assert run_record["raw_response_file"] == str(
        paths.raw_json
    )
    assert run_record["symbols_file"] == str(
        paths.symbols_text
    )


def test_empty_pipeline_writes_empty_symbols_file(
    tmp_path: Path,
) -> None:
    result = run_candidate_pipeline(
        [],
        FilterSettings(),
        source_name="empty_source",
        evaluated_at=EVALUATED_AT,
    )

    paths = write_candidate_outputs(
        output_dir=tmp_path,
        stem="empty-test",
        raw_data={},
        pipeline_result=result,
    )

    assert paths.symbols_text.read_text(
        encoding="utf-8"
    ) == ""


def test_extra_fields_cannot_replace_pipeline_fields() -> None:
    result = make_pipeline_result()

    with pytest.raises(
        ValueError,
        match="reserved names: accepted_count",
    ):
        build_pipeline_run_record(
            result,
            extra_fields={
                "accepted_count": 999,
            },
        )


@pytest.mark.parametrize(
    "stem",
    [
        "",
        "   ",
        r"folder\result",
        "folder/result",
    ],
)
def test_invalid_output_stem_is_rejected(
    tmp_path: Path,
    stem: str,
) -> None:
    with pytest.raises(ValueError):
        write_candidate_outputs(
            output_dir=tmp_path,
            stem=stem,
            raw_data={},
            pipeline_result=make_pipeline_result(),
        )
