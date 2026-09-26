"""Builders shared by the test modules."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from cheap_flights.config import Route
from cheap_flights.models import Leg, Offer, Place
from cheap_flights.places import ResolvedRoute


def make_route(**overrides: Any) -> Route:
    data: dict[str, Any] = {"name": "r1", "from": "IST", "to": ["LHR"], "alert": {"max_price": 100}}
    if "origin" in overrides:
        data["from"] = overrides.pop("origin")
    data.update(overrides)
    return Route.model_validate(data)


def make_resolved(
    route: Route | None = None,
    origins: tuple[str, ...] = ("IST",),
    destinations: tuple[str, ...] = ("LHR",),
) -> ResolvedRoute:
    return ResolvedRoute(
        route=route or make_route(),
        origins=tuple(Place(code, code) for code in origins),
        destinations=tuple(Place(code, code) for code in destinations),
    )


def make_offer(
    price: int,
    *,
    route: str = "r1",
    destination: str = "LHR",
    arrive_at: str = "LHR",
    depart: date = date(2026, 11, 2),
    return_date: date | None = None,
    currency: str = "EUR",
    legs: tuple[Leg, ...] | None = None,
    airlines: tuple[str, ...] = ("Turkish Airlines",),
) -> Offer:
    if legs is None:
        legs = (
            Leg(
                "IST",
                arrive_at,
                datetime.combine(depart, time(8, 0)),
                datetime.combine(depart, time(10, 30)),
                "TK",
                "1979",
            ),
        )
    return Offer(
        route=route,
        destination=destination,
        price=price,
        currency=currency,
        airlines=airlines,
        legs=legs,
        depart_date=depart,
        return_date=return_date,
        url="https://www.google.com/travel/flights/search?tfs=test",
    )
