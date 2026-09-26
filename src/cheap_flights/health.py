"""Track consecutive failed runs and decide when to send outage/reminder/recovery alerts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from .config import HealthConfig
from .runner import RunStats
from .store import Store

log = logging.getLogger(__name__)
STATE_KEY = "health"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _from_iso(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


@dataclass(frozen=True)
class HealthState:
    consecutive_failed_runs: int = 0
    outage_open: bool = False
    outage_alerted_at: datetime | None = None
    last_success_at: datetime | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "consecutive_failed_runs": self.consecutive_failed_runs,
                "outage_open": self.outage_open,
                "outage_alerted_at": _iso(self.outage_alerted_at),
                "last_success_at": _iso(self.last_success_at),
            }
        )

    @classmethod
    def from_json(cls, text: str) -> HealthState:
        data = json.loads(text)
        return cls(
            consecutive_failed_runs=int(data["consecutive_failed_runs"]),
            outage_open=bool(data["outage_open"]),
            outage_alerted_at=_from_iso(data["outage_alerted_at"]),
            last_success_at=_from_iso(data["last_success_at"]),
        )


class EventKind(StrEnum):
    OUTAGE = "outage"
    REMINDER = "reminder"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class HealthEvent:
    kind: EventKind
    failed_runs: int
    searches: int
    failures: int
    cause: str | None
    last_success_at: datetime | None


def advance(
    state: HealthState, stats: RunStats, now: datetime, config: HealthConfig
) -> tuple[HealthState, HealthEvent | None]:
    if stats.searches == 0:
        return state, None
    if not stats.is_failed():
        fresh = HealthState(last_success_at=now)
        if not state.outage_open:
            return fresh, None
        event = HealthEvent(
            EventKind.RECOVERED,
            state.consecutive_failed_runs,
            stats.searches,
            stats.failed,
            None,
            now,
        )
        return fresh, event
    failed_runs = state.consecutive_failed_runs + 1
    updated = replace(state, consecutive_failed_runs=failed_runs)
    kind: EventKind | None = None
    if failed_runs >= config.alert_after_failed_runs:
        if not state.outage_open:
            kind = EventKind.OUTAGE
        elif state.outage_alerted_at is None or now - state.outage_alerted_at >= timedelta(
            hours=config.reminder_hours
        ):
            kind = EventKind.REMINDER
    if kind is None:
        return updated, None
    updated = replace(updated, outage_open=True, outage_alerted_at=now)
    event = HealthEvent(
        kind,
        failed_runs,
        stats.searches,
        stats.failed,
        stats.dominant_failure(),
        state.last_success_at,
    )
    return updated, event


def load_state(store: Store) -> HealthState:
    text = store.get_value(STATE_KEY)
    if text is None:
        return HealthState()
    try:
        return HealthState.from_json(text)
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("stored health state is unreadable (%s); starting fresh", exc)
        return HealthState()


def save_state(store: Store, state: HealthState) -> None:
    store.set_value(STATE_KEY, state.to_json())
