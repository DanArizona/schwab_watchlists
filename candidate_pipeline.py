"""
Source-neutral candidate filtering pipeline.

The pipeline accepts candidates from any source, applies FilterSettings, and
returns a result suitable for command-line reporting, GUI display, audit files,
or eventual Watchlist submission.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from candidate_filters import (
    CandidateDecision,
    FilterResult,
    FilterSettings,
    filter_candidates,
)
from candidate_model import SymbolCandidate


@dataclass(frozen=True, slots=True)
class CandidatePipelineResult:
    """Complete result of one candidate-filtering run."""

    source_name: str
    evaluated_at: datetime
    candidates: tuple[SymbolCandidate, ...]
    settings: FilterSettings
    filter_result: FilterResult

    def __post_init__(self) -> None:
        normalized_source = self.source_name.strip()

        if not normalized_source:
            raise ValueError("Pipeline source_name cannot be empty.")

        object.__setattr__(self, "source_name", normalized_source)

    @property
    def input_count(self) -> int:
        """Number of candidates supplied to the pipeline."""

        return len(self.candidates)

    @property
    def accepted(self) -> tuple[SymbolCandidate, ...]:
        """Candidates accepted by all configured filters."""

        return self.filter_result.accepted

    @property
    def rejected(self) -> tuple[CandidateDecision, ...]:
        """Rejected candidate decisions, including reasons."""

        return self.filter_result.rejected

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def accepted_symbols(self) -> tuple[str, ...]:
        """Accepted symbols in pipeline order."""

        return tuple(candidate.symbol for candidate in self.accepted)

    @property
    def rejected_symbols(self) -> tuple[str, ...]:
        """Rejected symbols in pipeline order."""

        return tuple(
            decision.candidate.symbol
            for decision in self.rejected
        )

    def decision_for(
        self,
        symbol: str,
    ) -> CandidateDecision | None:
        """Return the decision for a symbol, ignoring case."""

        normalized_symbol = symbol.strip().upper()

        for decision in self.filter_result.decisions:
            if decision.candidate.symbol == normalized_symbol:
                return decision

        return None


def run_candidate_pipeline(
    candidates: Iterable[SymbolCandidate],
    settings: FilterSettings,
    *,
    source_name: str,
    evaluated_at: datetime | None = None,
) -> CandidatePipelineResult:
    """
    Apply filtering to an ordered candidate collection.

    Candidate order is preserved. Any maximum-result limit is applied by the
    filtering layer after scalar filters have been evaluated.
    """

    candidate_tuple = tuple(candidates)

    filter_result = filter_candidates(
        candidate_tuple,
        settings,
    )

    return CandidatePipelineResult(
        source_name=source_name,
        evaluated_at=evaluated_at or datetime.now().astimezone(),
        candidates=candidate_tuple,
        settings=settings,
        filter_result=filter_result,
    )
