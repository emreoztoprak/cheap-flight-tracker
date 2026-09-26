from collections import Counter
from datetime import UTC, datetime, timedelta

from cheap_flights.config import HealthConfig
from cheap_flights.health import EventKind, HealthState, advance, load_state, save_state
from cheap_flights.runner import RunStats
from cheap_flights.store import Store

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
CONFIG = HealthConfig(alert_after_failed_runs=2, reminder_hours=24)
FAILED = RunStats(searches=10, failures=Counter({"parse_error": 10}))
GOOD = RunStats(searches=10, ok=10)
EMPTY = RunStats()


def test_first_failure_is_quiet_second_opens_outage():
    state, event = advance(HealthState(), FAILED, NOW, CONFIG)
    assert event is None and state.consecutive_failed_runs == 1
    state, event = advance(state, FAILED, NOW + timedelta(hours=6), CONFIG)
    assert event.kind is EventKind.OUTAGE
    assert (event.failed_runs, event.searches, event.failures, event.cause) == (
        2,
        10,
        10,
        "parse_error",
    )
    assert state.outage_open and state.outage_alerted_at == NOW + timedelta(hours=6)


def test_reminder_only_after_reminder_hours():
    state = HealthState(consecutive_failed_runs=2, outage_open=True, outage_alerted_at=NOW)
    state, event = advance(state, FAILED, NOW + timedelta(hours=6), CONFIG)
    assert event is None and state.consecutive_failed_runs == 3
    state, event = advance(state, FAILED, NOW + timedelta(hours=24), CONFIG)
    assert event.kind is EventKind.REMINDER
    assert state.outage_alerted_at == NOW + timedelta(hours=24)


def test_recovery_after_outage():
    state = HealthState(consecutive_failed_runs=3, outage_open=True, outage_alerted_at=NOW)
    state, event = advance(state, GOOD, NOW + timedelta(hours=6), CONFIG)
    assert event.kind is EventKind.RECOVERED and event.failed_runs == 3
    assert state == HealthState(last_success_at=NOW + timedelta(hours=6))


def test_success_without_outage_is_quiet_and_resets_counter():
    state, event = advance(HealthState(consecutive_failed_runs=1), GOOD, NOW, CONFIG)
    assert event is None and state == HealthState(last_success_at=NOW)


def test_run_without_searches_changes_nothing():
    state = HealthState(consecutive_failed_runs=1, outage_open=True, outage_alerted_at=NOW)
    assert advance(state, EMPTY, NOW, CONFIG) == (state, None)


def test_threshold_of_one_alerts_immediately():
    _, event = advance(HealthState(), FAILED, NOW, HealthConfig(alert_after_failed_runs=1))
    assert event.kind is EventKind.OUTAGE


def test_state_round_trips_through_store():
    store = Store(":memory:")
    assert load_state(store) == HealthState()
    state = HealthState(2, True, NOW, NOW - timedelta(days=1))
    save_state(store, state)
    assert load_state(store) == state


def test_corrupt_state_falls_back_to_default():
    store = Store(":memory:")
    store.set_value("health", "{not json")
    assert load_state(store) == HealthState()
