import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from cheap_flights.google.client import URL, GoogleFlights, build_query, classify
from cheap_flights.google.parser import ParseError, parse_offers
from cheap_flights.models import FetchKind, Place, SearchJob
from tests.helpers import make_route

FIXTURES = Path(__file__).parent / "fixtures" / "google"
RESULTS_URL = "https://www.google.com/travel/flights?tfs=abc"


def job(**route_overrides):
    return SearchJob(
        make_route(**route_overrides), Place("IST", "IST"), Place("LHR", "LHR"), date(2026, 10, 25)
    )


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def reply(text, status=200, url=RESULTS_URL):
    return SimpleNamespace(status_code=status, url=url, text=text)


def results_page(price=150):
    leg = [None] * 23
    leg[3], leg[6], leg[8], leg[10] = "IST", "LHR", [9], [11]
    leg[20], leg[21], leg[22] = [2026, 10, 25], [2026, 10, 25], ["TK", "1"]
    flight = [None] * 23
    flight[1], flight[2] = ["Turkish Airlines"], [leg]
    payload = [None] * 10
    payload[2] = [[[flight, [[None, price], "t"]]]]
    return (
        "<html><body><script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', data:"
        + json.dumps(payload)
        + ", sideChannel: {}});</script></body></html>"
    )


def test_build_query_one_way_with_filters():
    query = build_query(job(trip="one-way", stops="direct", currency="TRY", depart_time="6-12"))
    assert query.get_trip_type() == "one-way"
    assert query.currency == "TRY"
    assert query.language == "en"
    (outbound,) = query.flight_data
    assert (outbound.from_airport.airport, outbound.to_airport.airport) == ("IST", "LHR")
    assert outbound.date == "2026-10-25"
    assert outbound.max_stops == 0
    assert (outbound.earliest_departure_hour, outbound.latest_departure_hour) == (6, 12)


def test_build_query_round_trip_adds_return_leg():
    round_trip = SearchJob(
        make_route(),
        Place("IST", "IST"),
        Place("London", "/m/04jpl"),
        date(2026, 10, 25),
        date(2026, 10, 30),
    )
    query = build_query(round_trip)
    assert query.get_trip_type() == "round-trip"
    outbound, back = query.flight_data
    assert (back.from_airport.airport, back.to_airport.airport, back.date) == (
        "/m/04jpl",
        "IST",
        "2026-10-30",
    )


@pytest.mark.parametrize(
    ("status", "url", "html", "kind"),
    [
        (200, "https://consent.google.com/m?continue=x", "", FetchKind.CONSENT_WALL),
        (200, RESULTS_URL, "<title>Before you continue</title>", FetchKind.CONSENT_WALL),
        (429, RESULTS_URL, "", FetchKind.BLOCKED),
        (200, "https://www.google.com/sorry/index", "", FetchKind.BLOCKED),
        (200, RESULTS_URL, "Our systems have detected unusual traffic", FetchKind.BLOCKED),
        (503, RESULTS_URL, "", FetchKind.NETWORK_ERROR),
        (404, RESULTS_URL, "", FetchKind.NETWORK_ERROR),
    ],
)
def test_classify_failures(status, url, html, kind):
    assert classify(status, url, html)[0] is kind


def test_classify_accepts_results_page():
    assert classify(200, RESULTS_URL, results_page()) is None


def test_search_ok():
    http = FakeHttp(reply(results_page(150)))
    result = GoogleFlights(http).search(job())
    assert result.kind is FetchKind.OK
    assert [o.price for o in result.offers] == [150]
    assert result.offers[0].url.startswith("https://www.google.com/travel/flights/search?tfs=")
    url, params = http.calls[0]
    assert url == URL
    assert set(params) == {"tfs", "hl", "curr"}


def test_search_no_flights():
    payload_page = (
        "<html><body><script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', data:"
        + json.dumps([None] * 10)
        + ", sideChannel: {}});</script></body></html>"
    )
    result = GoogleFlights(FakeHttp(reply(payload_page))).search(job())
    assert result.kind is FetchKind.NO_FLIGHTS


def test_search_network_exception_is_a_result_not_a_crash():
    result = GoogleFlights(FakeHttp(TimeoutError("operation timed out"))).search(job())
    assert result.kind is FetchKind.NETWORK_ERROR
    assert "timed out" in result.detail


def test_search_parse_error_saves_debug_html_and_keeps_five(tmp_path):
    http = FakeHttp(*[reply("<html>changed</html>") for _ in range(7)])
    google = GoogleFlights(http, debug_dir=tmp_path)
    for _ in range(7):
        assert google.search(job()).kind is FetchKind.PARSE_ERROR
    saved = list(tmp_path.glob("*.html"))
    assert len(saved) == 5
    assert saved[0].read_text() == "<html>changed</html>"


def test_captured_one_way_page_parses():
    offers = parse_offers((FIXTURES / "oneway.html").read_text(), job(), "u")
    assert len(offers) >= 3
    assert all(o.legs[0].origin == "IST" for o in offers)
    assert [o.price for o in offers] == sorted(o.price for o in offers)


def test_captured_round_trip_page_parses():
    assert parse_offers((FIXTURES / "roundtrip.html").read_text(), job(), "u")


def test_captured_no_results_page_is_empty():
    assert parse_offers((FIXTURES / "noresults.html").read_text(), job(), "u") == []


def test_captured_country_page_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_offers((FIXTURES / "country.html").read_text(), job(), "u")


def test_captured_consent_page_is_classified():
    html = (FIXTURES / "consent.html").read_text()
    assert classify(200, RESULTS_URL, html)[0] is FetchKind.CONSENT_WALL
