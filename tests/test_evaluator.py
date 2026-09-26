from datetime import UTC, date, datetime, timedelta

from cheap_flights.evaluator import evaluate, record_history
from cheap_flights.store import Store
from tests.helpers import make_offer, make_route

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def history(store, *prices, currency="EUR", days_ago_start=3):
    for i, price in enumerate(prices):
        store.add_price("r1", "LHR", NOW - timedelta(days=days_ago_start - i), price, currency)


def test_max_price_rule():
    route = make_route(alert={"max_price": 100})
    deal = evaluate(
        route, [make_offer(120), make_offer(95, depart=date(2026, 11, 3))], Store(":memory:"), NOW
    )
    assert deal.best_price == 95
    assert deal.reason == "at or below your limit of 100 EUR"
    assert [o.price for o in deal.offers] == [95, 120]


def test_above_max_price_is_no_deal():
    assert evaluate(make_route(), [make_offer(101)], Store(":memory:"), NOW) is None


def test_no_offers_is_no_deal():
    assert evaluate(make_route(), [], Store(":memory:"), NOW) is None


def test_drop_rule_needs_three_prior_runs():
    route = make_route(alert={"drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 220)
    assert evaluate(route, [make_offer(100)], store, NOW) is None
    history(store, 210, days_ago_start=1)
    deal = evaluate(route, [make_offer(150)], store, NOW)
    assert deal.reason == "25% below the 30-day low of 200 EUR"


def test_drop_rule_not_met():
    route = make_route(alert={"drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 220, 210)
    assert evaluate(route, [make_offer(161)], store, NOW) is None


def test_drop_rule_ignores_other_currency_and_old_history():
    route = make_route(alert={"drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 220, 210, currency="TRY")
    history(store, 200, 220, 210, days_ago_start=40)
    assert evaluate(route, [make_offer(10)], store, NOW) is None


def test_both_rules_are_reported():
    route = make_route(alert={"max_price": 100, "drop_percent": 20})
    store = Store(":memory:")
    history(store, 200, 200, 200)
    deal = evaluate(route, [make_offer(90)], store, NOW)
    assert deal.reason == "at or below your limit of 100 EUR; 55% below the 30-day low of 200 EUR"


def test_dedupe_until_cheaper_or_a_week_passes():
    route = make_route()
    store = Store(":memory:")
    store.record_alert("r1", NOW - timedelta(days=1), 90, "EUR")
    assert evaluate(route, [make_offer(95)], store, NOW) is None
    assert evaluate(route, [make_offer(90)], store, NOW) is None
    assert evaluate(route, [make_offer(85)], store, NOW) is not None
    assert evaluate(route, [make_offer(95)], store, NOW + timedelta(days=7)) is not None


def test_dedupe_ignores_alerts_in_another_currency():
    store = Store(":memory:")
    store.record_alert("r1", NOW - timedelta(days=1), 10, "TRY")
    assert evaluate(make_route(), [make_offer(95)], store, NOW) is not None


def test_top_offers_are_distinct_by_arrival_airport_and_date():
    route = make_route(top_n=2)
    offers = [
        make_offer(80),
        make_offer(85),  # same arrival + date as 80 → skipped
        make_offer(90, arrive_at="LGW"),
        make_offer(95, depart=date(2026, 11, 5)),
    ]
    deal = evaluate(route, offers, Store(":memory:"), NOW)
    assert [o.price for o in deal.offers] == [80, 90]


def test_record_history_stores_cheapest_per_destination():
    store = Store(":memory:")
    record_history(
        [make_offer(120), make_offer(100), make_offer(150, destination="AMS")], store, NOW
    )
    assert store.route_lows("r1", "EUR", NOW, NOW + timedelta(seconds=1)) == [100]
