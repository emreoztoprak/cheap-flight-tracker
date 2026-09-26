"""Real Google Flights search. Excluded by default; run with: pytest -m live"""

from datetime import date, timedelta

import pytest

from cheap_flights.google import GoogleFlights, make_http_client
from cheap_flights.models import FetchKind, Place, SearchJob
from tests.helpers import make_route


@pytest.mark.live
def test_live_search_returns_offers():
    job = SearchJob(
        make_route(trip="one-way"),
        Place("IST", "IST"),
        Place("London", "/m/04jpl"),
        date.today() + timedelta(days=30),
    )
    result = GoogleFlights(make_http_client(20)).search(job)
    assert result.kind is FetchKind.OK, result.detail
    assert all(offer.price > 0 for offer in result.offers)
