"""Turn user-written places (airport codes, city names, country codes) into search endpoints."""

from __future__ import annotations

import csv
import difflib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files

import yaml

from .config import ConfigError, Route
from .models import Place


@dataclass(frozen=True)
class Airport:
    iata: str
    name: str
    city: str
    country: str
    size: str  # "large" or "medium"
    runway_ft: int = 0  # longest runway; ranks airports by size within a size class


@dataclass(frozen=True)
class ResolvedRoute:
    route: Route
    origins: tuple[Place, ...]
    destinations: tuple[Place, ...]


def _is_airport_code(text: str) -> bool:
    return len(text) == 3 and text.isalpha() and text.isupper()


def _is_country_code(text: str) -> bool:
    return len(text) == 2 and text.isalpha() and text.isupper()


def _by_size(airport: Airport) -> tuple[bool, int, str]:
    return (airport.size != "large", -airport.runway_ft, airport.iata)


class PlaceIndex:
    def __init__(self, airports: Iterable[Airport], city_entities: Mapping[str, str]) -> None:
        self._airports: dict[str, Airport] = {}
        self._by_country: dict[str, list[Airport]] = defaultdict(list)
        self._by_city: dict[str, list[Airport]] = defaultdict(list)
        for airport in airports:
            self._airports[airport.iata] = airport
            self._by_country[airport.country].append(airport)
            if airport.city:
                self._by_city[airport.city.casefold()].append(airport)
        self._entities = {name.casefold(): (name, entity) for name, entity in city_entities.items()}

    @classmethod
    def bundled(cls) -> PlaceIndex:
        data = files("cheap_flights") / "data"
        with (data / "airports.csv").open(encoding="utf-8", newline="") as handle:
            airports = [
                Airport(**{**row, "runway_ft": int(row["runway_ft"] or 0)})
                for row in csv.DictReader(handle)
            ]
        entities = yaml.safe_load((data / "cities.yaml").read_text(encoding="utf-8"))
        return cls(airports, entities)

    def suggest(self, query: str, limit: int = 8) -> list[tuple[str, str]]:
        """(value, label) pairs for autocomplete: city entities, then codes, then names."""
        return _suggest(self, query, limit)

    def resolve_origin(self, text: str) -> list[Place]:
        text = text.strip()
        if _is_country_code(text):
            raise ConfigError(
                f"{text!r} is a country; the origin must be an airport code or a city"
            )
        return self._airport_or_city(text)

    def resolve_destination(self, text: str, max_per_country: int) -> list[Place]:
        text = text.strip()
        if _is_country_code(text):
            return self._country(text, max_per_country)
        return self._airport_or_city(text)

    def _airport_or_city(self, text: str) -> list[Place]:
        if _is_airport_code(text):
            if text not in self._airports:
                raise ConfigError(f"unknown airport code {text!r}")
            return [Place(label=text, code=text)]
        return self._city(text)

    def _country(self, code: str, limit: int) -> list[Place]:
        airports = sorted(self._by_country.get(code, []), key=_by_size)
        if not airports:
            raise ConfigError(
                f"unknown country code {code!r} (or it has no airports with scheduled flights)"
            )
        return [Place(label=a.iata, code=a.iata) for a in airports[:limit]]

    def _city(self, text: str) -> list[Place]:
        key = text.casefold()
        if key in self._entities:
            name, entity = self._entities[key]
            return [Place(label=name, code=entity)]
        airports = self._by_city.get(key)
        if airports:
            return [Place(label=a.iata, code=a.iata) for a in sorted(airports, key=_by_size)]
        known = sorted(
            {name for name, _ in self._entities.values()}
            | {a.city for a in self._airports.values() if a.city}
        )
        suggestions = difflib.get_close_matches(text, known, n=3)
        hint = f" (did you mean: {', '.join(suggestions)}?)" if suggestions else ""
        raise ConfigError(
            f"unknown place {text!r}{hint}; "
            "use an airport code like LHR, a city name, or a country code like DE"
        )


def _label(airport: Airport) -> str:
    return f"{airport.iata} — {airport.name} ({airport.city}, {airport.country})"


def _suggest(index: PlaceIndex, query: str, limit: int) -> list[tuple[str, str]]:
    text = query.strip().casefold()
    if len(text) < 2:
        return []
    found: list[tuple[str, str]] = []
    for key, (name, _entity) in sorted(index._entities.items()):
        if key.startswith(text):
            found.append((name, f"{name} — all airports"))
    airports = sorted(index._airports.values(), key=_by_size)
    for airport in airports:
        if airport.iata.casefold().startswith(text):
            found.append((airport.iata, _label(airport)))
    for airport in airports:
        haystack = f"{airport.name} {airport.city}".casefold()
        if not airport.iata.casefold().startswith(text) and text in haystack:
            found.append((airport.iata, _label(airport)))
    return found[:limit]


def resolve_routes(routes: Sequence[Route], index: PlaceIndex) -> list[ResolvedRoute]:
    """Resolve every route, collecting all errors before raising."""
    resolved: list[ResolvedRoute] = []
    errors: list[str] = []
    for route in routes:
        try:
            origins = index.resolve_origin(route.origin)
            origin_codes = {place.code for place in origins}
            destinations: list[Place] = []
            for text in route.to:
                for place in index.resolve_destination(text, route.max_airports_per_country):
                    if place.code not in origin_codes and place not in destinations:
                        destinations.append(place)
            if not destinations:
                raise ConfigError("no destinations left after removing the origin")
            resolved.append(ResolvedRoute(route, tuple(origins), tuple(destinations)))
        except ConfigError as exc:
            errors.append(f"routes.{route.name}: {exc}")
    if errors:
        raise ConfigError("\n".join(errors))
    return resolved
