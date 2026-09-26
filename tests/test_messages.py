from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from cheap_flights.health import EventKind, HealthEvent
from cheap_flights.messages import check_message, health_message, offer_line, report_message
from cheap_flights.models import Leg
from tests.helpers import make_offer, make_route


def test_direct_offer_line():
    line = offer_line(1, make_offer(89))
    assert line.text == "1. 89 EUR  IST→LHR  Mon 02 Nov 08:00→10:30  Turkish Airlines, direct"
    assert line.url.startswith("https://www.google.com/travel/flights")


def test_connecting_overnight_round_trip_line():
    legs = (
        Leg("IST", "WAW", datetime(2026, 11, 2, 18, 30), datetime(2026, 11, 2, 19, 5), "LO", "1"),
        Leg("WAW", "LHR", datetime(2026, 11, 2, 23, 15), datetime(2026, 11, 3, 0, 40), "LO", "2"),
    )
    offer = make_offer(174, legs=legs, airlines=("LOT",), return_date=date(2026, 11, 7))
    assert offer_line(2, offer).text == (
        "2. 174 EUR  IST→LHR  Mon 02 Nov 18:30→00:40+1  LOT, 1 stop (WAW)  · return Sat 07 Nov"
    )


def report(route, offers, previous=None, highlights=(), searches=3):
    from cheap_flights.evaluator import Report

    return Report(route, tuple(offers), previous, tuple(highlights), searches)


def test_report_message_with_change_and_round_trip_footer():
    route = make_route(name="mad-ist", origin="MAD", to=["IST"])
    message = report_message(report(route, [make_offer(281, return_date=date(2026, 11, 7))], 246))
    assert message.title == "✈️ mad-ist: 281 EUR  ↑ +35 EUR since last check (246 EUR)"
    assert message.lines[0].text.startswith("MAD → IST")
    assert message.lines[1].text.startswith("1. 281 EUR")
    assert message.footer == "Round-trip prices are the total for both directions."


def test_report_message_change_texts():
    route = make_route(trip="one-way")
    down = report_message(report(route, [make_offer(200)], 220))
    assert down.title.endswith("↓ −20 EUR since last check (220 EUR)")
    same = report_message(report(route, [make_offer(200)], 200))
    assert same.title.endswith("= same as last check")
    first = report_message(report(route, [make_offer(200)], None))
    assert first.title.endswith("first check") and first.footer == ""


def test_report_message_highlights_deals():
    route = make_route()
    message = report_message(report(route, [make_offer(90)], 131, ["below your limit of 100 EUR"]))
    assert (
        message.title
        == "🔥 r1: 90 EUR — below your limit of 100 EUR  ↓ −41 EUR since last check (131 EUR)"
    )


def test_report_message_without_flights():
    message = report_message(report(make_route(), [], 246, searches=30))
    assert message.title == "✈️ r1: no flights found this check (30 searches)"


def test_check_message():
    assert "test" in check_message().title


def test_outage_message():
    event = HealthEvent(
        EventKind.OUTAGE, 2, 48, 48, "parse_error", datetime(2026, 9, 26, 4, 0, tzinfo=UTC)
    )
    message = health_message(event, ZoneInfo("Europe/Madrid"))
    assert message.title == "⚠️ Cheap Flight Tracker: I can't fetch flight data"
    texts = [line.text for line in message.lines]
    assert texts[0] == "2 runs in a row failed (48/48 searches failed in the last run)."
    assert texts[1].startswith("Cause: parse_error. Google probably changed its page format")
    assert texts[2] == "Last successful run: 2026-09-26 06:00 CEST"


def test_reminder_and_recovery_messages():
    reminder = HealthEvent(EventKind.REMINDER, 5, 10, 10, "blocked", None)
    message = health_message(reminder, ZoneInfo("UTC"))
    assert message.title.endswith("(still failing)")
    assert message.lines[2].text == "Last successful run: never"
    recovered = HealthEvent(EventKind.RECOVERED, 3, 10, 0, None, None)
    message = health_message(recovered, ZoneInfo("UTC"))
    assert message.title == "✅ Cheap Flight Tracker: fetching flight data works again"
    assert message.lines[0].text == "Recovered after 3 failed run(s)."
