"""Summarise a route's results for the message sent after every check."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Route
from .models import Offer
from .store import Store

HISTORY_WINDOW = timedelta(days=30)
MIN_HISTORY_RUNS = 3


@dataclass(frozen=True)
class Report:
    route: Route
    offers: tuple[Offer, ...]  # cheapest first, at most route.top_n
    previous: int | None  # best price of the previous check (same currency), if any
    highlights: tuple[str, ...]  # met alert rules: price limit, % drop
    searches: int  # searches made for this route in this check

    @property
    def best_price(self) -> int | None:
        return self.offers[0].price if self.offers else None

    @property
    def change(self) -> int | None:
        if self.best_price is None or self.previous is None:
            return None
        return self.best_price - self.previous


def summarize(
    route: Route, offers: Sequence[Offer], store: Store, now: datetime, searches: int
) -> Report:
    """Call before record_history() for this check, so `previous` is the check before."""
    currency = route.currency
    earlier = store.route_lows(route.name, currency, since=now - timedelta(days=3650), before=now)
    previous = earlier[-1] if earlier else None
    ranked = sorted(offers, key=lambda offer: offer.price)
    highlights: list[str] = []
    if ranked:
        best = ranked[0].price
        rule = route.alert
        if rule.max_price is not None and best <= rule.max_price:
            highlights.append(f"below your limit of {rule.max_price} {currency}")
        if rule.drop_percent is not None:
            lows = store.route_lows(route.name, currency, since=now - HISTORY_WINDOW, before=now)
            if len(lows) >= MIN_HISTORY_RUNS:
                low = min(lows)
                if best <= low * (1 - rule.drop_percent / 100):
                    drop = round((1 - best / low) * 100)
                    highlights.append(f"{drop}% below the 30-day low of {low} {currency}")
    return Report(route, top_offers(ranked, route.top_n), previous, tuple(highlights), searches)


def record_history(offers: Sequence[Offer], store: Store, now: datetime) -> None:
    """Store the cheapest price per (route, destination) for this run."""
    cheapest: dict[tuple[str, str], Offer] = {}
    for offer in offers:
        key = (offer.route, offer.destination)
        if key not in cheapest or offer.price < cheapest[key].price:
            cheapest[key] = offer
    for (route, destination), offer in cheapest.items():
        store.add_price(route, destination, now, offer.price, offer.currency)


def top_offers(offers: Sequence[Offer], limit: int) -> tuple[Offer, ...]:
    """Cheapest offers, at most one per (arrival airport, departure date)."""
    ranked = sorted(offers, key=lambda offer: offer.price)
    picked: list[Offer] = []
    seen: set[tuple[str, object]] = set()
    for offer in ranked:
        key = (offer.legs[-1].destination, offer.depart_date)
        if key in seen:
            continue
        seen.add(key)
        picked.append(offer)
        if len(picked) == limit:
            break
    return tuple(picked)
