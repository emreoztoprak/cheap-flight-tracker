"""Plain data types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Route


@dataclass(frozen=True)
class Place:
    """A search endpoint: an airport (code "LHR") or a Google city entity (code "/m/04jpl")."""

    label: str
    code: str


@dataclass(frozen=True)
class SearchJob:
    route: Route
    origin: Place
    destination: Place
    depart_date: date
    return_date: date | None = None


@dataclass(frozen=True)
class Leg:
    origin: str
    destination: str
    depart: datetime  # local time at the origin airport
    arrive: datetime  # local time at the destination airport
    airline: str  # IATA airline code
    flight_number: str


@dataclass(frozen=True)
class Offer:
    route: str
    destination: str  # the searched destination's label, e.g. "London" or "BER"
    price: int  # round-trip total for round trips
    currency: str
    airlines: tuple[str, ...]
    legs: tuple[Leg, ...]  # outbound legs only
    depart_date: date
    return_date: date | None
    url: str  # Google Flights search link

    @property
    def stops(self) -> int:
        return len(self.legs) - 1


class FetchKind(StrEnum):
    OK = "ok"
    NO_FLIGHTS = "no_flights"
    CONSENT_WALL = "consent_wall"
    BLOCKED = "blocked"
    PARSE_ERROR = "parse_error"
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True)
class FetchResult:
    kind: FetchKind
    offers: tuple[Offer, ...] = ()
    detail: str = ""
