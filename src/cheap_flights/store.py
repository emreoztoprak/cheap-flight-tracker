"""SQLite state: price history, sent alerts, runs and small key/value records.

One connection is shared by the scheduler thread and the web UI, guarded by a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    route TEXT NOT NULL,
    destination TEXT NOT NULL,
    run_at TEXT NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_route ON price_history (route, currency, run_at);
CREATE TABLE IF NOT EXISTS alerts_sent (
    route TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    trigger TEXT NOT NULL,
    searches INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    no_flights INTEGER NOT NULL,
    failures TEXT NOT NULL
);
"""
_ROUTE_OFFERS_KEY = "offers:"
_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _ts(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).strftime(_FORMAT)


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _FORMAT).replace(tzinfo=UTC)


@dataclass(frozen=True)
class AlertRecord:
    sent_at: datetime
    price: int
    currency: str


@dataclass(frozen=True)
class SentAlert:
    route: str
    sent_at: datetime
    price: int
    currency: str
    reason: str


@dataclass(frozen=True)
class RunRecord:
    started_at: datetime
    finished_at: datetime
    trigger: str  # "scheduled" or "manual"
    searches: int
    ok: int
    no_flights: int
    failures: dict[str, int] = field(default_factory=dict)


class Store:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"{path} was written by a newer version (schema {version}); refusing to use it"
            )
        with self._db:
            if version == 1:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(alerts_sent)")}
                if "reason" not in columns:
                    self._db.execute(
                        "ALTER TABLE alerts_sent ADD COLUMN reason TEXT NOT NULL DEFAULT ''"
                    )
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)

    def _read(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add_price(
        self, route: str, destination: str, run_at: datetime, price: int, currency: str
    ) -> None:
        self._write(
            "INSERT INTO price_history VALUES (?, ?, ?, ?, ?)",
            (route, destination, _ts(run_at), price, currency),
        )

    def route_lows(self, route: str, currency: str, since: datetime, before: datetime) -> list[int]:
        """The lowest price of each run in [since, before), oldest run first."""
        rows = self._read(
            "SELECT MIN(price) FROM price_history"
            " WHERE route = ? AND currency = ? AND run_at >= ? AND run_at < ?"
            " GROUP BY run_at ORDER BY run_at",
            (route, currency, _ts(since), _ts(before)),
        )
        return [row[0] for row in rows]

    def record_alert(
        self, route: str, sent_at: datetime, price: int, currency: str, reason: str = ""
    ) -> None:
        self._write(
            "INSERT INTO alerts_sent (route, sent_at, price, currency, reason)"
            " VALUES (?, ?, ?, ?, ?)",
            (route, _ts(sent_at), price, currency, reason),
        )

    def last_alert(self, route: str, currency: str) -> AlertRecord | None:
        rows = self._read(
            "SELECT sent_at, price, currency FROM alerts_sent"
            " WHERE route = ? AND currency = ? ORDER BY sent_at DESC LIMIT 1",
            (route, currency),
        )
        return AlertRecord(_parse_ts(rows[0][0]), rows[0][1], rows[0][2]) if rows else None

    def get_value(self, key: str) -> str | None:
        rows = self._read("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def set_value(self, key: str, value: str) -> None:
        self._write(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def prune(self, before: datetime) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM price_history WHERE run_at < ?", (_ts(before),))
            self._db.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (_ts(before),))
            self._db.execute("DELETE FROM runs WHERE started_at < ?", (_ts(before),))

    def alerts(self, limit: int) -> list[SentAlert]:
        rows = self._read(
            "SELECT route, sent_at, price, currency, reason FROM alerts_sent"
            " ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        )
        return [SentAlert(r[0], _parse_ts(r[1]), r[2], r[3], r[4]) for r in rows]

    def price_series(self, route: str, since: datetime) -> list[tuple[datetime, str, int, str]]:
        """(run_at, destination, price, currency) rows for one route, oldest first."""
        rows = self._read(
            "SELECT run_at, destination, price, currency FROM price_history"
            " WHERE route = ? AND run_at >= ? ORDER BY run_at, destination",
            (route, _ts(since)),
        )
        return [(_parse_ts(r[0]), r[1], r[2], r[3]) for r in rows]

    def record_run(self, run: RunRecord) -> None:
        self._write(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _ts(run.started_at),
                _ts(run.finished_at),
                run.trigger,
                run.searches,
                run.ok,
                run.no_flights,
                json.dumps(run.failures, sort_keys=True),
            ),
        )

    def recent_runs(self, limit: int) -> list[RunRecord]:
        rows = self._read("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [
            RunRecord(_parse_ts(r[0]), _parse_ts(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6]))
            for r in rows
        ]

    def last_run(self) -> RunRecord | None:
        runs = self.recent_runs(1)
        return runs[0] if runs else None

    def set_route_offers(self, route: str, payload: dict) -> None:
        """Latest offers of a route, as JSON-ready data (shown on the dashboard)."""
        self.set_value(_ROUTE_OFFERS_KEY + route, json.dumps(payload))

    def route_offers(self, route: str) -> dict | None:
        text = self.get_value(_ROUTE_OFFERS_KEY + route)
        return None if text is None else json.loads(text)
