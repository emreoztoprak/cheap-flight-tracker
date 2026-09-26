"""One complete check: plan, search, evaluate, notify, update health."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from .config import Config
from .evaluator import HISTORY_RETENTION, evaluate, record_history
from .health import EventKind, advance, load_state, save_state
from .messages import deal_message, health_message
from .notify.base import Dispatcher
from .places import ResolvedRoute
from .planner import build_plan, departure_dates
from .runner import Fetcher, RunStats, execute
from .store import Store

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _never() -> bool:
    return False


def _nothing() -> None:
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

    def run_once(self) -> RunStats:
        started = self._clock()
        try:
            stats = self._check(started)
        except Exception:
            log.exception("run crashed")
            stats = RunStats(searches=1, failures=Counter({"internal_error": 1}))
        self._update_health(stats, self._clock())
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
        for resolved in self._routes:
            self._finish_route(resolved, output.offers.get(resolved.route.name, []), now)
        self._store.prune(now - HISTORY_RETENTION)
        return output.stats

    def _finish_route(self, resolved: ResolvedRoute, offers: list, now: datetime) -> None:
        route = resolved.route
        deal = evaluate(route, offers, self._store, now)
        record_history(offers, self._store, now)
        best = min((offer.price for offer in offers), default=None)
        log.info(
            "route %s: %d offers, best %s",
            route.name,
            len(offers),
            f"{best} {route.currency}" if best is not None else "none",
        )
        if deal is None:
            return
        delivery = self._dispatcher.send(deal_message(deal))
        if delivery.delivered:
            self._store.record_alert(route.name, now, deal.best_price, route.currency)
            log.info("deal alert sent for %s: %s", route.name, deal.reason)

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
