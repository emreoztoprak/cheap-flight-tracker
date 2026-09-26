from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from cheap_flights.health import EventKind, HealthEvent
from cheap_flights.messages import (
    change_text,
    check_message,
    health_message,
    money,
    report_message,
)
from cheap_flights.models import Leg
from tests.helpers import make_offer, make_route


def report(route, offers, previous=None, highlights=(), searches=3):
    from cheap_flights.evaluator import Report

    return Report(route, tuple(offers), previous, tuple(highlights), searches)


CHECKED = datetime(2026, 9, 26, 22, 40, tzinfo=ZoneInfo("Europe/Madrid"))
NEXT = datetime(2026, 9, 27, 8, 0, tzinfo=ZoneInfo("Europe/Madrid"))


def texts(message):
    return [(line.style, line.text) for line in message.lines]


def test_money_uses_symbols_for_common_currencies():
    assert (money(281, "EUR"), money(99, "USD"), money(50, "GBP")) == ("281 €", "$99", "£50")
    assert money(4200, "TRY") == "4200 TRY"


def test_round_trip_report_layout():
    route = make_route(name="madrid-to-istanbul", origin="MAD", to=["IST"])
    offers = [
        make_offer(281, depart=date(2026, 10, 23), return_date=date(2026, 10, 30)),
        make_offer(289, depart=date(2026, 9, 30), return_date=date(2026, 10, 7)),
    ]
    message = report_message(
        report(route, offers, 281, ["Under your 500 € limit"]),
        places="Madrid (MAD) → Istanbul (IST)",
        checked=CHECKED,
        next_check=NEXT,
    )
    assert message.title == "🔥 madrid-to-istanbul — 281 €"
    assert texts(message) == [
        ("text", "➖ Same as last check"),
        ("text", "🎯 Under your 500 € limit"),
        ("note", "Madrid (MAD) → Istanbul (IST) · round trip · price for both ways"),
        ("gap", ""),
        ("option", "🥇 281 € · Fri 23 Oct → Fri 30 Oct (7 nights)"),
        ("detail", "Turkish Airlines · direct · 08:00 → 10:30"),
        ("link", "View on Google Flights ›"),
        ("gap", ""),
        ("option", "🥈 289 € · Wed 30 Sep → Wed 07 Oct (7 nights)"),
        ("detail", "Turkish Airlines · direct · 08:00 → 10:30"),
        ("link", "View on Google Flights ›"),
    ]
    assert message.lines[6].url.startswith("https://www.google.com/travel/flights")
    assert message.footer == "Checked Sat 26 Sep 22:40 · next check Sun 27 Sep 08:00"


def test_one_way_connecting_offer_with_airports_shown():
    legs = (
        Leg("IST", "WAW", datetime(2026, 11, 2, 18, 30), datetime(2026, 11, 2, 19, 5), "LO", "1"),
        Leg("WAW", "LHR", datetime(2026, 11, 2, 23, 15), datetime(2026, 11, 3, 0, 40), "LO", "2"),
    )
    offer = make_offer(174, legs=legs, airlines=("LOT",))
    message = report_message(report(make_route(trip="one-way"), [offer]), show_airports=True)
    assert texts(message)[1:4] == [
        ("note", "IST → LHR · one way"),
        ("gap", ""),
        ("option", "🥇 174 € · Mon 02 Nov"),
    ]
    assert texts(message)[4] == ("detail", "IST→LHR · LOT · 1 stop (WAW) · 18:30 → 00:40+1")
    assert message.footer == ""


def test_change_lines():
    route = make_route(trip="one-way")
    first = lambda message: message.lines[0].text  # noqa: E731
    assert first(report_message(report(route, [make_offer(281)], 246))) == (
        "📈 +35 € since last check (was 246 €)"
    )
    assert first(report_message(report(route, [make_offer(200)], 220))) == (
        "📉 −20 € since last check (was 220 €)"
    )
    assert first(report_message(report(route, [make_offer(200)], None))) == "🆕 First check"
    plain = report_message(report(route, [make_offer(200)], 200))
    assert plain.title == "✈️ r1 — 200 €"


def test_change_text_is_plain_for_history():
    route = make_route()
    assert (
        change_text(report(route, [make_offer(281)], 246)) == "+35 € since last check (was 246 €)"
    )
    assert change_text(report(route, [make_offer(200)], 200)) == "same as last check"
    assert change_text(report(route, [make_offer(200)], None)) == "first check"


def test_fourth_option_is_numbered():
    offers = [make_offer(100 + i) for i in range(4)]
    message = report_message(report(make_route(trip="one-way", top_n=4), offers))
    options = [text for style, text in texts(message) if style == "option"]
    assert [o.split(" ")[0] for o in options] == ["🥇", "🥈", "🥉", "4."]


def test_report_message_without_flights():
    message = report_message(report(make_route(), [], 246, searches=30), checked=CHECKED)
    assert message.title == "✈️ r1 — no flights found"
    assert texts(message)[0] == ("text", "30 searches, nothing matched your filters")
    assert message.footer == "Checked Sat 26 Sep 22:40"


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
