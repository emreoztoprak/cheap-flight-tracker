import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cheap_flights.store import AlertRecord, RunRecord, Store

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


def test_runs_are_recorded_newest_first():
    store = Store(":memory:")
    assert store.last_run() is None
    first = RunRecord(T0, T0 + timedelta(minutes=5), "scheduled", 10, 9, 0, {"blocked": 1})
    second = RunRecord(
        T0 + timedelta(hours=6), T0 + timedelta(hours=6, minutes=4), "manual", 10, 10, 0, {}
    )
    store.record_run(first)
    store.record_run(second)
    assert store.last_run() == second
    assert store.recent_runs(5) == [second, first]


def test_route_offers_round_trip_as_json():
    store = Store(":memory:")
    assert store.route_offers("r") is None
    store.set_route_offers("r", {"run_at": "x", "offers": [{"price": 241}]})
    assert store.route_offers("r") == {"run_at": "x", "offers": [{"price": 241}]}


def test_alert_log_includes_route_and_reason():
    store = Store(":memory:")
    store.record_alert("a", T0, 100, "EUR", "at or below your limit of 120 EUR")
    store.record_alert("b", T0 + timedelta(hours=1), 90, "EUR")
    log = store.alerts(10)
    assert [(a.route, a.price, a.reason) for a in log] == [
        ("b", 90, ""),
        ("a", 100, "at or below your limit of 120 EUR"),
    ]


def test_price_series_returns_rows_oldest_first():
    store = Store(":memory:")
    store.add_price("r", "EZE", T0, 900, "EUR")
    store.add_price("r", "AEP", T0, 950, "EUR")
    store.add_price("r", "EZE", T0 + timedelta(hours=6), 880, "EUR")
    store.add_price("other", "EZE", T0, 1, "EUR")
    assert store.price_series("r", since=T0 - timedelta(days=1)) == [
        (T0, "AEP", 950, "EUR"),
        (T0, "EZE", 900, "EUR"),
        (T0 + timedelta(hours=6), "EZE", 880, "EUR"),
    ]


def test_version_1_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "state.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE price_history (route TEXT, destination TEXT, run_at TEXT, price INTEGER,"
        " currency TEXT);"
        "CREATE TABLE alerts_sent (route TEXT, sent_at TEXT, price INTEGER, currency TEXT);"
        "CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO alerts_sent VALUES ('r', '2026-09-26T06:00:00.000000Z', 120, 'EUR');"
        "PRAGMA user_version = 1;"
    )
    db.close()
    store = Store(path)
    assert store.last_alert("r", "EUR").price == 120
    assert store.alerts(5)[0].reason == ""
    store.record_run(RunRecord(T0, T0, "scheduled", 0, 0, 0, {}))
    assert store.last_run().trigger == "scheduled"


def test_store_can_be_used_from_another_thread():
    import threading

    store = Store(":memory:")
    errors = []

    def worker():
        try:
            store.add_price("r", "LHR", T0, 1, "EUR")
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == [] and store.route_lows("r", "EUR", T0, T0 + timedelta(1)) == [1]
