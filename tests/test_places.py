import pytest

from cheap_flights.config import ConfigError
from cheap_flights.models import Place
from cheap_flights.places import Airport, PlaceIndex, resolve_routes
from tests.helpers import make_route

AIRPORTS = [
    Airport("IST", "Istanbul Airport", "Istanbul", "TR", "large"),
    Airport("SAW", "Sabiha Gokcen", "Istanbul", "TR", "large"),
    Airport("ESB", "Esenboga", "Ankara", "TR", "large"),
    Airport("FRA", "Frankfurt", "Frankfurt", "DE", "large"),
    Airport("MUC", "Munich", "Munich", "DE", "large"),
    Airport("DTM", "Dortmund", "Dortmund", "DE", "medium"),
    Airport("BER", "Berlin Brandenburg", "Berlin", "DE", "large"),
    Airport("LHR", "Heathrow", "London", "GB", "large"),
]
INDEX = PlaceIndex(AIRPORTS, {"London": "/m/04jpl"})


def test_airport_code():
    assert INDEX.resolve_destination("LHR", 10) == [Place("LHR", "LHR")]


def test_unknown_airport_code():
    with pytest.raises(ConfigError, match="unknown airport code 'XXX'"):
        INDEX.resolve_destination("XXX", 10)


def test_country_lists_large_airports_first_and_caps():
    assert [p.code for p in INDEX.resolve_destination("DE", 2)] == ["BER", "FRA"]
    assert [p.code for p in INDEX.resolve_destination("DE", 10)] == ["BER", "FRA", "MUC", "DTM"]


def test_unknown_country():
    with pytest.raises(ConfigError, match="unknown country code 'ZZ'"):
        INDEX.resolve_destination("ZZ", 10)


def test_city_with_entity_is_one_search():
    assert INDEX.resolve_destination("london", 10) == [Place("London", "/m/04jpl")]


def test_city_without_entity_uses_its_airports():
    assert INDEX.resolve_destination("Istanbul", 10) == [Place("IST", "IST"), Place("SAW", "SAW")]


def test_unknown_city_suggests_close_matches():
    with pytest.raises(ConfigError, match="did you mean: Frankfurt"):
        INDEX.resolve_destination("Frankfrt", 10)


def test_origin_cannot_be_a_country():
    with pytest.raises(ConfigError, match="is a country"):
        INDEX.resolve_origin("DE")


def test_resolve_routes_drops_destinations_equal_to_origin():
    (resolved,) = resolve_routes([make_route(to=["TR"])], INDEX)
    assert resolved.origins == (Place("IST", "IST"),)
    assert [p.code for p in resolved.destinations] == ["ESB", "SAW"]


def test_resolve_routes_reports_every_bad_route():
    routes = [make_route(name="a", to=["XXX"]), make_route(name="b", origin="DE")]
    with pytest.raises(ConfigError) as info:
        resolve_routes(routes, INDEX)
    assert "routes.a: unknown airport code 'XXX'" in str(info.value)
    assert "routes.b:" in str(info.value)


def test_bundled_index_loads():
    index = PlaceIndex.bundled()
    assert index.resolve_origin("IST") == [Place("IST", "IST")]
    assert index.resolve_destination("London", 10) == [Place("London", "/m/04jpl")]
    germany = [p.code for p in index.resolve_destination("DE", 10)]
    assert {"FRA", "MUC", "BER", "HAM"} <= set(germany) and len(germany) == 10
