"""Pure scheduling policy for deciding when a Watchlist cycle is due."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from market_clock import MARKET_TIMEZONE_NAME
from watchlist_cycle_index import discover_watchlist_cycles

SCHEDULE_STATUS_DUE = "DUE"
SCHEDULE_STATUS_NOT_DUE = "NOT_DUE"
SCHEDULE_STATUS_OUTSIDE_SESSION = "OUTSIDE_SESSION"
SCHEDULE_STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
SCHEDULE_STATUS_NO_PRIOR_CYCLE = "NO_PRIOR_CYCLE"

SCHEDULE_PHASE_OPENING = "opening"
SCHEDULE_PHASE_EARLY = "early"
SCHEDULE_PHASE_REGULAR = "regular"
SCHEDULE_PHASE_CLOSED = "closed"
SCHEDULE_PHASE_NON_TRADING_DAY = "non_trading_day"


@dataclass(frozen=True, slots=True)
class WatchlistCycleSchedulePolicy:
    """Configurable weekday/session cadence for Watchlist cycles."""

    timezone_name: str = MARKET_TIMEZONE_NAME
    session_start: time = time(9, 30)
    session_end: time = time(16, 0)
    opening_phase: timedelta = timedelta(minutes=10)
    early_phase: timedelta = timedelta(minutes=60)
    opening_interval: timedelta = timedelta(minutes=1)
    early_interval: timedelta = timedelta(minutes=5)
    regular_interval: timedelta = timedelta(minutes=10)
    trading_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})

    def __post_init__(self) -> None:
        if self.session_start >= self.session_end:
            raise ValueError("Session start must be before session end.")
        if self.opening_phase <= timedelta(0):
            raise ValueError("Opening phase must be positive.")
        if self.early_phase <= self.opening_phase:
            raise ValueError(
                "Early phase must end after the opening phase."
            )
        for name, value in (
            ("opening interval", self.opening_interval),
            ("early interval", self.early_interval),
            ("regular interval", self.regular_interval),
        ):
            if value <= timedelta(0):
                raise ValueError(f"{name.capitalize()} must be positive.")
        if not self.trading_weekdays:
            raise ValueError("At least one trading weekday is required.")
        if any(day < 0 or day > 6 for day in self.trading_weekdays):
            raise ValueError("Trading weekdays must be in the range 0 through 6.")

    @property
    def timezone(self) -> ZoneInfo:
        """Return the configured session timezone."""

        return ZoneInfo(self.timezone_name)


DEFAULT_WATCHLIST_CYCLE_SCHEDULE = WatchlistCycleSchedulePolicy()


@dataclass(frozen=True, slots=True)
class WatchlistCycleScheduleDecision:
    """One deterministic decision about whether a cycle is due."""

    status: str
    due: bool
    reason: str
    evaluated_at: datetime
    session_start: datetime
    session_end: datetime
    phase: str
    interval: timedelta | None
    last_cycle_id: str | None = None
    last_cycle_started_at: datetime | None = None
    next_due_at: datetime | None = None


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information.")


def _session_bounds(
    now: datetime,
    policy: WatchlistCycleSchedulePolicy,
) -> tuple[datetime, datetime, datetime]:
    _require_aware(now, field_name="now")
    local_now = now.astimezone(policy.timezone)
    session_start = datetime.combine(
        local_now.date(),
        policy.session_start,
        tzinfo=policy.timezone,
    )
    session_end = datetime.combine(
        local_now.date(),
        policy.session_end,
        tzinfo=policy.timezone,
    )
    return local_now, session_start, session_end


def _phase_at(
    value: datetime,
    *,
    session_start: datetime,
    policy: WatchlistCycleSchedulePolicy,
) -> str:
    elapsed = value - session_start
    if elapsed < policy.opening_phase:
        return SCHEDULE_PHASE_OPENING
    if elapsed < policy.early_phase:
        return SCHEDULE_PHASE_EARLY
    return SCHEDULE_PHASE_REGULAR


def _interval_after(
    value: datetime,
    *,
    session_start: datetime,
    policy: WatchlistCycleSchedulePolicy,
) -> timedelta:
    phase = _phase_at(
        value,
        session_start=session_start,
        policy=policy,
    )
    if phase == SCHEDULE_PHASE_OPENING:
        return policy.opening_interval
    if phase == SCHEDULE_PHASE_EARLY:
        return policy.early_interval
    return policy.regular_interval


def evaluate_watchlist_cycle_schedule(
    *,
    now: datetime,
    last_cycle_started_at: datetime | None = None,
    last_cycle_id: str | None = None,
    policy: WatchlistCycleSchedulePolicy = (
        DEFAULT_WATCHLIST_CYCLE_SCHEDULE
    ),
) -> WatchlistCycleScheduleDecision:
    """Evaluate the schedule without reading files or contacting services."""

    local_now, session_start, session_end = _session_bounds(now, policy)

    if local_now.weekday() not in policy.trading_weekdays:
        return WatchlistCycleScheduleDecision(
            status=SCHEDULE_STATUS_NON_TRADING_DAY,
            due=False,
            reason="The configured session does not run on this weekday.",
            evaluated_at=local_now,
            session_start=session_start,
            session_end=session_end,
            phase=SCHEDULE_PHASE_NON_TRADING_DAY,
            interval=None,
        )

    if not (session_start <= local_now < session_end):
        return WatchlistCycleScheduleDecision(
            status=SCHEDULE_STATUS_OUTSIDE_SESSION,
            due=False,
            reason="The current time is outside the configured market session.",
            evaluated_at=local_now,
            session_start=session_start,
            session_end=session_end,
            phase=SCHEDULE_PHASE_CLOSED,
            interval=None,
        )

    current_phase = _phase_at(
        local_now,
        session_start=session_start,
        policy=policy,
    )

    if last_cycle_started_at is None:
        return WatchlistCycleScheduleDecision(
            status=SCHEDULE_STATUS_NO_PRIOR_CYCLE,
            due=True,
            reason="No Watchlist cycle has run during the current session.",
            evaluated_at=local_now,
            session_start=session_start,
            session_end=session_end,
            phase=current_phase,
            interval=None,
        )

    _require_aware(
        last_cycle_started_at,
        field_name="last_cycle_started_at",
    )
    local_last = last_cycle_started_at.astimezone(policy.timezone)

    if not (session_start <= local_last < session_end):
        return WatchlistCycleScheduleDecision(
            status=SCHEDULE_STATUS_NO_PRIOR_CYCLE,
            due=True,
            reason="No Watchlist cycle has run during the current session.",
            evaluated_at=local_now,
            session_start=session_start,
            session_end=session_end,
            phase=current_phase,
            interval=None,
        )

    interval = _interval_after(
        local_last,
        session_start=session_start,
        policy=policy,
    )
    next_due_at = local_last + interval
    due = local_now >= next_due_at

    return WatchlistCycleScheduleDecision(
        status=(
            SCHEDULE_STATUS_DUE
            if due
            else SCHEDULE_STATUS_NOT_DUE
        ),
        due=due,
        reason=(
            "The configured interval has elapsed since the last cycle."
            if due
            else "The configured interval has not elapsed since the last cycle."
        ),
        evaluated_at=local_now,
        session_start=session_start,
        session_end=session_end,
        phase=current_phase,
        interval=interval,
        last_cycle_id=last_cycle_id,
        last_cycle_started_at=local_last,
        next_due_at=next_due_at,
    )


def _parse_cycle_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def evaluate_output_watchlist_cycle_schedule(
    output_dir: Path,
    *,
    now: datetime | None = None,
    policy: WatchlistCycleSchedulePolicy = (
        DEFAULT_WATCHLIST_CYCLE_SCHEDULE
    ),
) -> WatchlistCycleScheduleDecision:
    """Evaluate the policy using the newest cycle from today's session."""

    effective_now = now or datetime.now(tz=policy.timezone)
    local_now, session_start, session_end = _session_bounds(
        effective_now,
        policy,
    )

    # Outside-session decisions do not need cycle history. This also lets the
    # checker report a closed session before an output directory exists.
    if (
        local_now.weekday() not in policy.trading_weekdays
        or not (session_start <= local_now < session_end)
    ):
        return evaluate_watchlist_cycle_schedule(
            now=local_now,
            policy=policy,
        )

    resolved_output_dir = output_dir.expanduser().resolve(strict=False)
    if not resolved_output_dir.is_dir():
        return evaluate_watchlist_cycle_schedule(
            now=local_now,
            policy=policy,
        )

    index = discover_watchlist_cycles(resolved_output_dir)
    latest_id: str | None = None
    latest_time: datetime | None = None

    for cycle in index.cycles:
        started_at = _parse_cycle_time(cycle.started_at)
        if started_at is None:
            continue
        local_started = started_at.astimezone(policy.timezone)
        if not (session_start <= local_started <= local_now):
            continue
        if latest_time is None or local_started > latest_time:
            latest_time = local_started
            latest_id = cycle.cycle_id

    return evaluate_watchlist_cycle_schedule(
        now=local_now,
        last_cycle_started_at=latest_time,
        last_cycle_id=latest_id,
        policy=policy,
    )


def format_watchlist_cycle_schedule_decision(
    decision: WatchlistCycleScheduleDecision,
) -> tuple[str, ...]:
    """Format a decision consistently for command-line tools."""

    interval_text = (
        f"{int(decision.interval.total_seconds())} seconds"
        if decision.interval is not None
        else "n/a"
    )
    return (
        "Watchlist cycle schedule",
        "=" * 72,
        f"Evaluated         : {decision.evaluated_at.isoformat(timespec='seconds')}",
        f"Market timezone   : {getattr(decision.session_start.tzinfo, 'key', str(decision.session_start.tzinfo))}",
        f"Session           : {decision.session_start.strftime('%H:%M')} - {decision.session_end.strftime('%H:%M')} {decision.session_start.tzname()}",
        f"Phase             : {decision.phase}",
        f"Decision          : {decision.status}",
        f"Cycle due         : {'yes' if decision.due else 'no'}",
        f"Required interval : {interval_text}",
        f"Last cycle        : {decision.last_cycle_id or '(none)'}",
        f"Last cycle at     : {decision.last_cycle_started_at.isoformat(timespec='seconds') if decision.last_cycle_started_at is not None else '(none)'}",
        f"Next due at       : {decision.next_due_at.isoformat(timespec='seconds') if decision.next_due_at is not None else '(n/a)'}",
        f"Reason            : {decision.reason}",
    )
