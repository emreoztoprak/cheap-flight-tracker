"""Data shaped for the templates: status bar, route cards, history charts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from ..coordinator import Coordinator, Progress
from ..health import load_state
from ..store import RunRecord

FAILURE_TEXT = {
    "consent_wall": "Google's cookie-consent page",
    "blocked": "blocked / rate-limited by Google",
    "parse_error": "Google changed its page",
    "network_error": "no connection to Google",
    "internal_error": "internal error (see logs)",
}


@dataclass(frozen=True)
class Status:
    state: str  # setup | running | failing | healthy | waiting
    headline: str
    detail: str
    progress: Progress
    last_run: RunRecord | None
    next_run: datetime | None
    tz: ZoneInfo


def tz_of(coordinator: Coordinator) -> ZoneInfo:
    loaded = coordinator.loaded
    return loaded.config.tz if loaded else ZoneInfo("UTC")


def fmt(moment: datetime | None, tz: ZoneInfo, pattern: str = "%a %d %b %H:%M") -> str:
    return moment.astimezone(tz).strftime(pattern) if moment else "—"


def next_run(coordinator: Coordinator, now: datetime) -> datetime | None:
    schedule = coordinator.schedule()
    if schedule is None:
        return None
    cron, tz = schedule
    return croniter(cron, now.astimezone(tz)).get_next(datetime)


def run_summary(run: RunRecord) -> str:
    failed = sum(run.failures.values())
    text = f"{run.searches} searches, {run.ok} with offers"
    if run.no_flights:
        text += f", {run.no_flights} without flights"
    if failed:
        text += f", {failed} failed"
    return text


def status(coordinator: Coordinator, now: datetime) -> Status:
    tz = tz_of(coordinator)
    progress = coordinator.progress()
    last = coordinator.store.last_run()
    upcoming = next_run(coordinator, now)
    if coordinator.loaded is None:
        return Status(
            "setup",
            "Not set up yet",
            "Add a notification channel and a route to start.",
            progress,
            last,
            None,
            tz,
        )
    if progress.running:
        total = f"/{progress.total}" if progress.total else ""
        return Status(
            "running",
            "Checking prices…",
            f"{progress.done}{total} searches done",
            progress,
            last,
            upcoming,
            tz,
        )
    health = load_state(coordinator.store)
    if health.outage_open or (
        last and last.searches and sum(last.failures.values()) / last.searches > 0.9
    ):
        cause = max(last.failures, key=last.failures.get) if last and last.failures else ""
        return Status(
            "failing",
            "Can't fetch flight data",
            FAILURE_TEXT.get(cause, cause or "see the logs"),
            progress,
            last,
            upcoming,
            tz,
        )
    if last is None:
        return Status(
            "waiting",
            "Ready",
            "No check has run yet — press Run now or wait for the schedule.",
            progress,
            None,
            upcoming,
            tz,
        )
    return Status(
        "healthy", "Working", f"Last check: {run_summary(last)}", progress, last, upcoming, tz
    )


def route_cards(coordinator: Coordinator, now: datetime) -> list[dict[str, Any]]:
    loaded = coordinator.loaded
    if loaded is None:
        return []
    tz = loaded.config.tz
    cards = []
    for resolved in loaded.routes:
        route = resolved.route
        saved = coordinator.store.route_offers(route.name) or {}
        offers = saved.get("offers") or []
        alert = coordinator.store.last_alert(route.name, route.currency)
        spark = run_lows(coordinator, route.name, now - timedelta(days=30), tz)
        cards.append(
            {
                "index": len(cards),
                "name": route.name,
                "title": f"{route.origin} → {', '.join(route.to)}",
                "trip": "round trip" if route.trip == "round-trip" else "one way",
                "best": offers[0] if offers else None,
                "currency": saved.get("currency", route.currency),
                "others": offers[1:],
                "checked": fmt(datetime.fromisoformat(saved["run_at"]), tz)
                if saved.get("run_at")
                else None,
                "alert": f"{alert.price} {alert.currency} on {fmt(alert.sent_at, tz)}"
                if alert
                else None,
                "spark": spark,
            }
        )
    return cards


def run_lows(
    coordinator: Coordinator, route: str, since: datetime, tz: ZoneInfo
) -> dict[str, list]:
    """Cheapest price of each run (all destinations), for sparklines."""
    lows: dict[datetime, int] = {}
    for run_at, _destination, price, _currency in coordinator.store.price_series(route, since):
        lows[run_at] = min(price, lows.get(run_at, price))
    ordered = sorted(lows.items())
    return {
        "labels": [fmt(t, tz, "%d %b %H:%M") for t, _ in ordered],
        "data": [p for _, p in ordered],
    }


def history_series(coordinator: Coordinator, route: str, since: datetime) -> dict[str, Any]:
    tz = tz_of(coordinator)
    rows = coordinator.store.price_series(route, since)
    times = sorted({run_at for run_at, *_ in rows})
    index = {t: i for i, t in enumerate(times)}
    by_destination: dict[str, list[int | None]] = {}
    currency = ""
    for run_at, destination, price, row_currency in rows:
        series = by_destination.setdefault(destination, [None] * len(times))
        series[index[run_at]] = price
        currency = row_currency
    return {
        "labels": [fmt(t, tz, "%d %b %H:%M") for t in times],
        "datasets": [{"label": d, "data": data} for d, data in sorted(by_destination.items())],
        "currency": currency,
    }
