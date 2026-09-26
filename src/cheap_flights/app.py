"""One complete check: plan, search, evaluate, notify, update health."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from croniter import croniter

from .config import Config
from .evaluator import record_history, summarize, top_offers
from .health import EventKind, advance, load_state, save_state
from .messages import change_text, health_message, offer_summary, report_message
from .notify.base import Dispatcher
from .places import ResolvedRoute
from .planner import build_plan, departure_dates
from .runner import Fetcher, RunStats, execute
from .store import RunRecord, Store

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _never() -> bool:
    return False


def _nothing() -> None:
    return None


def _ignore(_total: int) -> None:
    return None


class App:
    def __init__(
        self,
        config: Config,
        routes: Sequence[ResolvedRoute],
        fetcher: Fetcher,
        store: Store,
        dispatcher: Dispatcher,
        *,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] = _never,
        on_progress: Callable[[], None] = _nothing,
        on_planned: Callable[[int], None] = _ignore,
        trigger: str = "scheduled",
    ) -> None:
        self._config = config
        self._routes = list(routes)
        self._fetcher = fetcher
        self._store = store
        self._dispatcher = dispatcher
        self._clock = clock
        self._sleep = sleep
        self._should_stop = should_stop
        self._on_progress = on_progress
        self._on_planned = on_planned
        self._trigger = trigger

    def run_once(self) -> RunStats:
        started = self._clock()
        try:
            stats = self._check(started)
        except Exception:
            log.exception("run crashed")
            stats = RunStats(searches=1, failures=Counter({"internal_error": 1}))
        finished = self._clock()
        self._store.record_run(
            RunRecord(
                started_at=started,
                finished_at=finished,
                trigger=self._trigger,
                searches=stats.searches,
                ok=stats.ok,
                no_flights=stats.no_flights,
                failures=dict(stats.failures),
            )
        )
        self._update_health(stats, finished)
        log.info(
            "run finished %s duration=%.0fs",
            stats.summary(),
            (self._clock() - started).total_seconds(),
        )
        return stats

    def _check(self, now: datetime) -> RunStats:
        search = self._config.search
        today = now.astimezone(self._config.tz).date()
        for resolved in self._routes:
            if not departure_dates(resolved.route, today):
                log.warning(
                    "route %s has no dates to search "
                    "(window is in the past or no allowed weekdays)",
                    resolved.route.name,
                )
        plan = build_plan(self._routes, today, search.max_searches_per_run)
        if plan.step > 1:
            log.warning(
                "routes need %d searches but max_searches_per_run is %d; "
                "searching every %d. departure date (%d searches)",
                plan.requested,
                search.max_searches_per_run,
                plan.step,
                len(plan.jobs),
            )
        if len(plan.jobs) > search.max_searches_per_run:
            log.warning(
                "even one date per route needs %d searches, above max_searches_per_run=%d",
                len(plan.jobs),
                search.max_searches_per_run,
            )
        log.info("run started: %d searches across %d routes", len(plan.jobs), len(self._routes))
        self._on_planned(len(plan.jobs))
        output = execute(
            plan.jobs,
            self._fetcher,
            retries=search.retries,
            delay_seconds=search.delay_seconds,
            sleep=self._sleep,
            should_stop=self._should_stop,
            on_progress=self._on_progress,
        )
        if output.stopped:
            log.warning("run interrupted by shutdown after %d searches", output.stats.searches)
        planned = Counter(job.route.name for job in plan.jobs)
        for resolved in self._routes:
            name = resolved.route.name
            self._finish_route(
                resolved,
                output.offers.get(name, []),
                now,
                searches=planned[name],
                answered=output.answered[name],
            )
        self._store.prune(now - timedelta(days=self._config.history_days))
        return output.stats

    def _finish_route(
        self, resolved: ResolvedRoute, offers: list, now: datetime, *, searches: int, answered: int
    ) -> None:
        route = resolved.route
        report = summarize(route, offers, self._store, now, searches)
        record_history(offers, self._store, now)
        self._store.set_route_offers(
            route.name,
            {
                "run_at": now.isoformat(),
                "currency": route.currency,
                "offers": [
                    {
                        "price": offer.price,
                        "line": offer_summary(offer),
                        "url": offer.url,
                        "destination": offer.legs[-1].destination,
                        "depart_date": offer.depart_date.isoformat(),
                    }
                    for offer in top_offers(offers, route.top_n)
                ],
            },
        )
        best = min((offer.price for offer in offers), default=None)
        log.info(
            "route %s: %d offers, best %s",
            route.name,
            len(offers),
            f"{best} {route.currency}" if best is not None else "none",
        )
        if not answered:
            # Nothing came back for this route (not searched, or every search failed):
            # a report would be misleading; failures are covered by health alerts.
            return
        tz = self._config.tz
        checked = now.astimezone(tz)
        message = report_message(
            report,
            places=resolved.places,
            show_airports=resolved.multi_airport,
            checked=checked,
            next_check=croniter(self._config.schedule, checked).get_next(datetime),
        )
        delivery = self._dispatcher.send(message)
        if delivery.delivered:
            reason = "; ".join(report.highlights) or change_text(report)
            self._store.record_alert(
                route.name, now, report.best_price or 0, route.currency, reason
            )
            log.info("report sent for %s: %s", route.name, reason)

    def _update_health(self, stats: RunStats, now: datetime) -> None:
        state = load_state(self._store)
        new_state, event = advance(state, stats, now, self._config.health)
        if event is not None:
            log.warning("health %s (cause: %s)", event.kind, event.cause)
            delivery = self._dispatcher.send(health_message(event, self._config.tz))
            if not delivery.delivered and event.kind is not EventKind.RECOVERED:
                # Nobody was told: keep the outage "unsent" so the next run tries again.
                new_state = replace(
                    new_state,
                    outage_open=state.outage_open,
                    outage_alerted_at=state.outage_alerted_at,
                )
        save_state(self._store, new_state)
