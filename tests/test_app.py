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


def test_run_offers_and_alert_reason_are_stored_for_the_dashboard():
    app, store, _ = build(priced(80))
    totals = []
    app = App(
        config(),
        [make_resolved(make_route(trip="one-way", window={"next_days": 2}))],
        Fetcher(priced(80)),
        store,
        Dispatcher([Inbox()]),
        clock=lambda: NOW,
        sleep=lambda s: None,
        on_planned=totals.append,
        trigger="manual",
    )
    app.run_once()
    assert totals == [2]
    run = store.last_run()
    assert (run.trigger, run.searches, run.ok, run.failures) == ("manual", 2, 2, {})
    saved = store.route_offers("r1")
    assert saved["run_at"] == NOW.isoformat()
    assert saved["currency"] == "EUR"
    assert [o["price"] for o in saved["offers"]] == [80, 80]  # one per departure date
    first = saved["offers"][0]
    assert first["line"].startswith("80 EUR  IST→LHR")
    assert first["url"].startswith("https://www.google.com/travel/flights")
    assert store.alerts(5)[0].reason == "at or below your limit of 100 EUR"


def test_crashed_run_is_recorded_too():
    def explode(job):
        raise RuntimeError("bug")

    app, store, _ = build(explode)
    app.run_once()
    assert store.last_run().failures == {"internal_error": 1}


def test_history_is_kept_for_the_configured_number_of_days():
    def run_with(days):
        store = Store(":memory:")
        store.add_price("r1", "LHR", NOW - timedelta(days=100), 50, "EUR")
        cfg = Config.model_validate({**config().model_dump(by_alias=True), "history_days": days})
        App(
            cfg,
            [make_resolved(make_route(trip="one-way", window={"next_days": 1}))],
            Fetcher(priced(150)),
            store,
            Dispatcher([Inbox()]),
            clock=lambda: NOW,
            sleep=lambda s: None,
        ).run_once()
        return store.route_lows("r1", "EUR", NOW - timedelta(days=365), NOW)

    assert run_with(120) == [50]
    assert run_with(90) == []
