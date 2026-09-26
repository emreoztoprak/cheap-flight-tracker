from datetime import UTC, date, datetime, timedelta

from cheap_flights.evaluator import record_history, summarize
from cheap_flights.store import Store
from tests.helpers import make_offer, make_route

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def history(store, *prices, currency="EUR", days_ago_start=3):
    for i, price in enumerate(prices):
        store.add_price("r1", "LHR", NOW - timedelta(days=days_ago_start - i), price, currency)


def test_report_always_has_the_cheapest_offers():
    report = summarize(
        make_route(alert={}),
        [make_offer(120), make_offer(95, depart=date(2026, 11, 3))],
        Store(":memory:"),
        NOW,
        searches=4,
    )
    assert [o.price for o in report.offers] == [95, 120]
    assert (report.best_price, report.previous, report.highlights, report.searches) == (
        95,
        None,
        (),
        4,
    )


def test_report_without_offers():
    report = summarize(make_route(alert={}), [], Store(":memory:"), NOW, searches=30)
    assert report.offers == () and report.best_price is None and report.change is None


def test_change_is_against_the_previous_check_in_the_same_currency():
    store = Store(":memory:")
    history(store, 200, 246)
    history(store, 10, currency="TRY", days_ago_start=0)
    report = summarize(make_route(alert={}), [make_offer(281)], store, NOW, searches=1)
    assert (report.previous, report.change) == (246, 35)


def test_limit_and_drop_are_highlights_not_filters():
    route = make_route(alert={"max_price": 100, "drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 200, 200)
    report = summarize(route, [make_offer(90)], store, NOW, searches=1)
    assert report.highlights == (
        "Under your 100 € limit",
        "55% below the 30-day low (200 €)",
    )
    quiet = summarize(route, [make_offer(190)], store, NOW, searches=1)
    assert quiet.highlights == () and quiet.best_price == 190


def test_drop_highlight_needs_three_earlier_checks():
    route = make_route(alert={"drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 220)
    assert summarize(route, [make_offer(100)], store, NOW, searches=1).highlights == ()


def test_top_offers_are_distinct_by_arrival_airport_and_date():
    route = make_route(top_n=2)
    offers = [
        make_offer(80),
        make_offer(85),
        make_offer(90, arrive_at="LGW"),
        make_offer(95, depart=date(2026, 11, 5)),
    ]
    report = summarize(route, offers, Store(":memory:"), NOW, searches=1)
    assert [o.price for o in report.offers] == [80, 90]


def test_record_history_stores_cheapest_per_destination():
    store = Store(":memory:")
    record_history(
        [make_offer(120), make_offer(100), make_offer(150, destination="AMS")], store, NOW
    )
    assert store.route_lows("r1", "EUR", NOW, NOW + timedelta(seconds=1)) == [100]
