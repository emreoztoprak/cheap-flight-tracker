from datetime import UTC, datetime, timedelta

from cheap_flights.app import App
from cheap_flights.config import Config
from cheap_flights.health import load_state
from cheap_flights.models import FetchKind, FetchResult
from cheap_flights.notify.base import Dispatcher, NotifyError
from cheap_flights.store import Store
from tests.helpers import make_offer, make_resolved, make_route

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def config(threshold=2):
    return Config.model_validate(
        {
            "routes": [{"name": "r1", "from": "IST", "to": ["LHR"], "alert": {"max_price": 100}}],
            "notify": {"telegram": {"bot_token": "123456:abcdefghij", "chat_id": "1"}},
            "search": {"delay_seconds": 0, "retries": 0},
            "health": {"alert_after_failed_runs": threshold},
        }
    )


class Inbox:
    name = "inbox"

    def __init__(self, fail=False):
        self.messages, self.fail = [], fail

    def send(self, message):
        if self.fail:
            raise NotifyError("down")
        self.messages.append(message)

    @property
    def titles(self):
        return [m.title for m in self.messages]


class Fetcher:
    def __init__(self, respond):
        self.respond = respond

    def search(self, job):
        return self.respond(job)


def priced(price):
    return lambda job: FetchResult(
        FetchKind.OK, (make_offer(price, depart=job.depart_date, return_date=job.return_date),)
    )


def build(respond, *, route=None, threshold=2, inbox=None, clock=None):
    inbox = inbox or Inbox()
    store = Store(":memory:")
    route = route or make_route(trip="one-way", window={"next_days": 2})
    app = App(
        config(threshold),
        [make_resolved(route)],
        Fetcher(respond),
        store,
        Dispatcher([inbox]),
        clock=clock or (lambda: NOW),
        sleep=lambda s: None,
    )
    return app, store, inbox


def test_deal_is_sent_once_and_recorded():
    app, store, inbox = build(priced(80))
    stats = app.run_once()
    assert (stats.searches, stats.ok) == (2, 2)
    assert inbox.titles == ["✈️ IST → LHR: 80 EUR (at or below your limit of 100 EUR)"]
    assert store.last_alert("r1", "EUR").price == 80
    app.run_once()
    assert len(inbox.messages) == 1  # dedupe


def test_no_deal_above_limit_but_history_recorded():
    app, store, inbox = build(priced(150))
    app.run_once()
    assert inbox.messages == []
    assert store.route_lows("r1", "EUR", NOW - timedelta(1), NOW + timedelta(1)) == [150]


def test_failed_delivery_is_not_recorded_so_it_retries_next_run():
    app, store, _ = build(priced(80), inbox=Inbox(fail=True))
    app.run_once()
    assert store.last_alert("r1", "EUR") is None


def test_outage_then_recovery():
    state = {"kind": FetchKind.PARSE_ERROR}
    clock = {"now": NOW}

    def respond(job):
        if state["kind"] is FetchKind.OK:
            return priced(500)(job)
        return FetchResult(state["kind"], detail="changed")

    app, store, inbox = build(respond, clock=lambda: clock["now"])
    assert app.run_once().is_failed()
    assert inbox.messages == []
    clock["now"] += timedelta(hours=6)
    app.run_once()
    assert inbox.titles == ["⚠️ Cheap Flight Tracker: I can't fetch flight data"]
    assert "Cause: parse_error" in inbox.messages[0].lines[1].text
    state["kind"] = FetchKind.OK
    clock["now"] += timedelta(hours=6)
    app.run_once()
    assert inbox.titles[-1] == "✅ Cheap Flight Tracker: fetching flight data works again"
    assert load_state(store).consecutive_failed_runs == 0


def test_crash_inside_a_run_becomes_internal_error_alert():
    def explode(job):
        raise RuntimeError("bug")

    app, _, inbox = build(explode, threshold=1)
    stats = app.run_once()
    assert stats.failures == {"internal_error": 1}
    assert "Cause: internal_error" in inbox.messages[0].lines[1].text


def test_route_without_dates_does_not_touch_health():
    route = make_route(trip="one-way", window={"from": "2026-09-01", "to": "2026-09-10"})
    app, store, inbox = build(priced(80), route=route, threshold=1)
    stats = app.run_once()
    assert stats.searches == 0
    assert inbox.messages == []
    assert load_state(store).last_success_at is None


def test_outage_alert_is_retried_next_run_when_no_channel_delivered():
    inbox = Inbox(fail=True)
    app, store, _ = build(lambda job: FetchResult(FetchKind.BLOCKED), threshold=1, inbox=inbox)
    app.run_once()
    assert inbox.messages == []
    assert not load_state(store).outage_open
    inbox.fail = False
    app.run_once()
    assert inbox.titles == ["⚠️ Cheap Flight Tracker: I can't fetch flight data"]
    assert load_state(store).outage_open
