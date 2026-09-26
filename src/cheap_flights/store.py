"""SQLite state: price history, sent alerts and small key/value records."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
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
    currency TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
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


class Store:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path))
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"{path} was written by a newer version (schema {version}); refusing to use it"
            )
        with self._db:
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._db.close()

    def add_price(
        self, route: str, destination: str, run_at: datetime, price: int, currency: str
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO price_history VALUES (?, ?, ?, ?, ?)",
                (route, destination, _ts(run_at), price, currency),
            )

    def route_lows(self, route: str, currency: str, since: datetime, before: datetime) -> list[int]:
        """The lowest price of each run in [since, before), oldest run first."""
        rows = self._db.execute(
            "SELECT MIN(price) FROM price_history"
            " WHERE route = ? AND currency = ? AND run_at >= ? AND run_at < ?"
            " GROUP BY run_at ORDER BY run_at",
            (route, currency, _ts(since), _ts(before)),
        ).fetchall()
        return [row[0] for row in rows]

    def record_alert(self, route: str, sent_at: datetime, price: int, currency: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO alerts_sent VALUES (?, ?, ?, ?)",
                (route, _ts(sent_at), price, currency),
            )

    def last_alert(self, route: str, currency: str) -> AlertRecord | None:
        row = self._db.execute(
            "SELECT sent_at, price, currency FROM alerts_sent"
            " WHERE route = ? AND currency = ? ORDER BY sent_at DESC LIMIT 1",
            (route, currency),
        ).fetchone()
        return None if row is None else AlertRecord(_parse_ts(row[0]), row[1], row[2])

    def get_value(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_value(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def prune(self, before: datetime) -> None:
        with self._db:
            self._db.execute("DELETE FROM price_history WHERE run_at < ?", (_ts(before),))
            self._db.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (_ts(before),))
