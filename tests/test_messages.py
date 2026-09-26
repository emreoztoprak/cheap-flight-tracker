from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from cheap_flights.evaluator import Deal
from cheap_flights.health import EventKind, HealthEvent
from cheap_flights.messages import check_message, deal_message, health_message, offer_line
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


def test_deal_message():
    route = make_route(to=["DE", "London"])
    deal = Deal(
        route, (make_offer(89, return_date=date(2026, 11, 7)),), "at or below your limit of 100 EUR"
    )
    message = deal_message(deal)
    assert message.title == "✈️ IST → DE, London: 89 EUR (at or below your limit of 100 EUR)"
    assert len(message.lines) == 1
    assert message.footer == "Round-trip prices are the total for both directions."


def test_plain_rendering():
    message = deal_message(Deal(make_route(trip="one-way"), (make_offer(89),), "why"))
    text = message.plain()
    assert text.startswith("✈️ IST → LHR: 89 EUR (why)\n\n1. 89 EUR")
    assert "   https://www.google.com/travel/flights" in text
    assert message.footer == ""


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
