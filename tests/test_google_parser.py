import json
from datetime import date, datetime

import pytest

from cheap_flights.google.parser import ParseError, parse_offers
from cheap_flights.models import Place, SearchJob
from tests.helpers import make_route

JOB = SearchJob(make_route(), Place("IST", "IST"), Place("London", "/m/04jpl"), date(2026, 10, 25))
URL = "https://www.google.com/travel/flights/search?tfs=x"


def leg(frm, to, dep, arr, code="TK", number="1979", day=(2026, 10, 25), arr_day=None):
    s = [None] * 23
    s[3], s[4], s[5], s[6] = frm, f"{frm} airport", f"{to} airport", to
    s[8], s[10], s[11] = dep, arr, 65
    s[20], s[21] = list(day), list(arr_day or day)
    s[22] = [code, number, None, "Airline"]
    return s


def item(price, legs, airlines=("Turkish Airlines",)):
    flight = [None] * 23
    flight[0], flight[1], flight[2] = "TK", list(airlines), legs
    return [flight, [[None, price], "booking-token"]]


def page(best=None, other=None, raw_data=None):
    payload = [None] * 10
    payload[2] = [best] if best is not None else None
    payload[3] = [other] if other is not None else None
    data = raw_data if raw_data is not None else json.dumps(payload)
    return (
        '<html><body><script class="ds:1" nonce="n">'
        f"AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});"
        "</script></body></html>"
    )


def test_reads_best_and_other_lists_sorted_by_price():
    html = page(
        best=[item(179, [leg("IST", "LHR", [8, 40], [10, 5])])],
        other=[
            item(
                174,
                [
                    leg("IST", "WAW", [18, 30], [19, 5], "LO", "136"),
                    leg("WAW", "LHR", [20, 15], [None, 31], "LO", "285", arr_day=(2026, 10, 26)),
                ],
                airlines=("LOT",),
            )
        ],
    )
    offers = parse_offers(html, JOB, URL)
    assert [o.price for o in offers] == [174, 179]
    cheapest = offers[0]
    assert cheapest.stops == 1
    assert cheapest.airlines == ("LOT",)
    assert cheapest.destination == "London"
    assert (cheapest.route, cheapest.currency, cheapest.url) == ("r1", "EUR", URL)
    assert cheapest.legs[0].depart == datetime(2026, 10, 25, 18, 30)
    assert cheapest.legs[1].arrive == datetime(2026, 10, 26, 0, 31)
    assert cheapest.legs[1].flight_number == "285"
    assert offers[1].legs[0].depart == datetime(2026, 10, 25, 8, 40)


def test_hour_only_and_missing_times_mean_zero_minutes():
    offers = parse_offers(page(best=[item(99, [leg("IST", "LHR", [8], None)])]), JOB, URL)
    assert offers[0].legs[0].depart == datetime(2026, 10, 25, 8, 0)
    assert offers[0].legs[0].arrive == datetime(2026, 10, 25, 0, 0)


def test_items_without_price_or_with_bad_shape_are_skipped():
    good = item(120, [leg("IST", "LHR", [9], [11])])
    no_price = item(None, [leg("IST", "LHR", [7], [9])])
    broken = [None, None]
    offers = parse_offers(page(best=[no_price, good], other=[broken]), JOB, URL)
    assert [o.price for o in offers] == [120]


def test_all_items_unreadable_is_a_parse_error():
    with pytest.raises(ParseError, match="could not read any of 2 flights"):
        parse_offers(page(best=[[None], ["x"]]), JOB, URL)


def test_duplicates_across_lists_are_removed():
    same = item(150, [leg("IST", "LHR", [9], [11])])
    assert len(parse_offers(page(best=[same], other=[same]), JOB, URL)) == 1


def test_empty_lists_mean_no_flights():
    assert parse_offers(page(), JOB, URL) == []


def test_error_status_means_no_flights():
    html = (
        "<html><body><script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', "
        "data:null, errorHasStatus: true, sideChannel: {}});</script></body></html>"
    )
    assert parse_offers(html, JOB, URL) == []


@pytest.mark.parametrize(
    ("html", "error"),
    [
        ("<html><body>nothing here</body></html>", "results script"),
        (page(raw_data='["only", "two"]'), "unexpected results structure"),
        (page(raw_data="[not json"), "not JSON"),
    ],
)
def test_non_results_pages_raise(html, error):
    with pytest.raises(ParseError, match=error):
        parse_offers(html, JOB, URL)


def test_moved_result_sections_are_a_parse_error():
    payload = [None] * 10
    payload[2] = {"moved": True}
    with pytest.raises(ParseError, match="unexpected results section"):
        parse_offers(page(raw_data=json.dumps(payload)), JOB, URL)


def test_flights_without_any_price_are_a_parse_error():
    no_price = item(None, [leg("IST", "LHR", [7], [9])])
    with pytest.raises(ParseError, match="none of 2 flights has a price"):
        parse_offers(page(best=[no_price], other=[no_price]), JOB, URL)
