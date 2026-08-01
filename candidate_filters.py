"""
Reusable source-neutral filters for Watchlist candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from candidate_model import SymbolCandidate


class MissingFieldPolicy(str, Enum):
    """
    Behavior when an enabled filter needs a field that is missing.

    REJECT is the safer default for Watchlist generation.
    """

    REJECT = "reject"
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class FilterSettings:
    """
    Minimal scalar filters.

    Percentage limits are expressed as percentage points:

        min_percent_change=5.0 means at least 5%
    """

    min_price: float | None = None
    max_price: float | None = None

    min_volume: int | None = None

    min_percent_change: float | None = None
    max_percent_change: float | None = None

    max_results: int | None = None

    missing_field_policy: MissingFieldPolicy = MissingFieldPolicy.REJECT

    def __post_init__(self) -> None:
        if self.min_price is not None and self.min_price < 0:
            raise ValueError("min_price cannot be negative.")

        if self.max_price is not None and self.max_price < 0:
            raise ValueError("max_price cannot be negative.")

        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price.")

        if self.min_volume is not None and self.min_volume < 0:
            raise ValueError("min_volume cannot be negative.")

        if (
            self.min_percent_change is not None
            and self.max_percent_change is not None
            and self.min_percent_change > self.max_percent_change
        ):
            raise ValueError(
                "min_percent_change cannot exceed max_percent_change."
            )

        if self.max_results is not None and self.max_results < 1:
            raise ValueError("max_results must be at least 1.")


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """Filtering decision for one candidate."""

    candidate: SymbolCandidate
    accepted: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Complete filtering result, including rejection explanations."""

    decisions: tuple[CandidateDecision, ...]

    @property
    def accepted(self) -> tuple[SymbolCandidate, ...]:
        return tuple(
            decision.candidate
            for decision in self.decisions
            if decision.accepted
        )

    @property
    def rejected(self) -> tuple[CandidateDecision, ...]:
        return tuple(
            decision
            for decision in self.decisions
            if not decision.accepted
        )


def _missing_reason(
    *,
    field_name: str,
    filter_description: str,
    policy: MissingFieldPolicy,
) -> str | None:
    """Return a rejection reason when a required value is missing."""

    if policy is MissingFieldPolicy.ALLOW:
        return None

    return (
        f"{field_name} is missing; cannot evaluate "
        f"{filter_description}"
    )


def evaluate_candidate(
    candidate: SymbolCandidate,
    settings: FilterSettings,
) -> CandidateDecision:
    """Apply the configured scalar filters to one candidate."""

    reasons: list[str] = []

    if settings.min_price is not None:
        if candidate.last_price is None:
            reason = _missing_reason(
                field_name="last price",
                filter_description=(
                    f"minimum price {settings.min_price:g}"
                ),
                policy=settings.missing_field_policy,
            )
            if reason:
                reasons.append(reason)

        elif candidate.last_price < settings.min_price:
            reasons.append(
                f"price {candidate.last_price:g} is below "
                f"minimum {settings.min_price:g}"
            )

    if settings.max_price is not None:
        if candidate.last_price is None:
            reason = _missing_reason(
                field_name="last price",
                filter_description=(
                    f"maximum price {settings.max_price:g}"
                ),
                policy=settings.missing_field_policy,
            )
            if reason:
                reasons.append(reason)

        elif candidate.last_price > settings.max_price:
            reasons.append(
                f"price {candidate.last_price:g} is above "
                f"maximum {settings.max_price:g}"
            )

    if settings.min_volume is not None:
        if candidate.volume is None:
            reason = _missing_reason(
                field_name="volume",
                filter_description=(
                    f"minimum volume {settings.min_volume:,}"
                ),
                policy=settings.missing_field_policy,
            )
            if reason:
                reasons.append(reason)

        elif candidate.volume < settings.min_volume:
            reasons.append(
                f"volume {candidate.volume:,} is below "
                f"minimum {settings.min_volume:,}"
            )

    if settings.min_percent_change is not None:
        if candidate.percent_change is None:
            reason = _missing_reason(
                field_name="percent change",
                filter_description=(
                    f"minimum change "
                    f"{settings.min_percent_change:g}%"
                ),
                policy=settings.missing_field_policy,
            )
            if reason:
                reasons.append(reason)

        elif candidate.percent_change < settings.min_percent_change:
            reasons.append(
                f"change {candidate.percent_change:g}% is below "
                f"minimum {settings.min_percent_change:g}%"
            )

    if settings.max_percent_change is not None:
        if candidate.percent_change is None:
            reason = _missing_reason(
                field_name="percent change",
                filter_description=(
                    f"maximum change "
                    f"{settings.max_percent_change:g}%"
                ),
                policy=settings.missing_field_policy,
            )
            if reason:
                reasons.append(reason)

        elif candidate.percent_change > settings.max_percent_change:
            reasons.append(
                f"change {candidate.percent_change:g}% is above "
                f"maximum {settings.max_percent_change:g}%"
            )

    return CandidateDecision(
        candidate=candidate,
        accepted=not reasons,
        reasons=tuple(reasons),
    )


def filter_candidates(
    candidates: Iterable[SymbolCandidate],
    settings: FilterSettings,
) -> FilterResult:
    """
    Filter candidates while preserving their incoming order.

    max_results is applied after all scalar filters. Candidates beyond the
    limit are retained as rejected decisions so the reason remains visible.
    """

    decisions: list[CandidateDecision] = []
    accepted_count = 0

    for candidate in candidates:
        decision = evaluate_candidate(candidate, settings)

        if decision.accepted:
            if (
                settings.max_results is not None
                and accepted_count >= settings.max_results
            ):
                decision = CandidateDecision(
                    candidate=candidate,
                    accepted=False,
                    reasons=(
                        f"result limit {settings.max_results} reached",
                    ),
                )
            else:
                accepted_count += 1

        decisions.append(decision)

    return FilterResult(decisions=tuple(decisions))
