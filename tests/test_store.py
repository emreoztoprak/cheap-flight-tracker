import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cheap_flights.store import AlertRecord, Store

T0 = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)


def test_route_lows_are_per_run_minimums_filtered_by_currency_and_time():
    store = Store(":memory:")
    store.add_price("r", "LHR", T0, 200, "EUR")
    store.add_price("r", "AMS", T0, 150, "EUR")
    store.add_price("r", "LHR", T0 + timedelta(hours=6), 180, "EUR")
    store.add_price("r", "LHR", T0 + timedelta(hours=6), 90, "TRY")
    store.add_price("other", "LHR", T0, 10, "EUR")
    lows = store.route_lows("r", "EUR", since=T0, before=T0 + timedelta(hours=12))
    assert lows == [150, 180]
    assert store.route_lows("r", "EUR", since=T0, before=T0 + timedelta(hours=6)) == [150]


def test_last_alert_is_newest_for_currency():
    store = Store(":memory:")
    assert store.last_alert("r", "EUR") is None
    store.record_alert("r", T0, 120, "EUR")
    store.record_alert("r", T0 + timedelta(days=1), 110, "EUR")
    store.record_alert("r", T0 + timedelta(days=2), 999, "TRY")
    assert store.last_alert("r", "EUR") == AlertRecord(T0 + timedelta(days=1), 110, "EUR")


def test_key_values():
    store = Store(":memory:")
    assert store.get_value("health") is None
    store.set_value("health", "a")
    store.set_value("health", "b")
    assert store.get_value("health") == "b"


def test_prune_removes_old_rows():
    store = Store(":memory:")
    store.add_price("r", "LHR", T0 - timedelta(days=100), 1, "EUR")
    store.add_price("r", "LHR", T0, 2, "EUR")
    store.record_alert("r", T0 - timedelta(days=100), 1, "EUR")
    store.prune(T0 - timedelta(days=90))
    assert store.route_lows(
        "r", "EUR", since=T0 - timedelta(days=365), before=T0 + timedelta(1)
    ) == [2]
    assert store.last_alert("r", "EUR") is None


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        Store(":memory:").add_price("r", "LHR", datetime(2026, 1, 1), 1, "EUR")


def test_data_survives_reopen(tmp_path):
    path = tmp_path / "state.db"
    store = Store(path)
    store.set_value("k", "v")
    store.close()
    assert Store(path).get_value("k") == "v"


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "state.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(RuntimeError, match="newer version"):
        Store(path)
