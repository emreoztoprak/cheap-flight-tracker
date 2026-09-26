"""Decide whether a route's results are a deal worth sending."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Route
from .models import Offer
from .store import Store

HISTORY_WINDOW = timedelta(days=30)
MIN_HISTORY_RUNS = 3
REPEAT_AFTER = timedelta(days=7)


@dataclass(frozen=True)
class Deal:
    route: Route
    offers: tuple[Offer, ...]  # cheapest first, at most route.top_n
    reason: str

    @property
    def best_price(self) -> int:
        return self.offers[0].price


def evaluate(route: Route, offers: Sequence[Offer], store: Store, now: datetime) -> Deal | None:
    if not offers:
        return None
    ranked = sorted(offers, key=lambda offer: offer.price)
    best = ranked[0].price
    currency = route.currency
    rule = route.alert
    reasons: list[str] = []
    if rule.max_price is not None and best <= rule.max_price:
        reasons.append(f"at or below your limit of {rule.max_price} {currency}")
    if rule.drop_percent is not None:
        lows = store.route_lows(route.name, currency, since=now - HISTORY_WINDOW, before=now)
        if len(lows) >= MIN_HISTORY_RUNS:
            low = min(lows)
            if best <= low * (1 - rule.drop_percent / 100):
                drop = round((1 - best / low) * 100)
                reasons.append(f"{drop}% below the 30-day low of {low} {currency}")
    if not reasons:
        return None
    last = store.last_alert(route.name, currency)
    if last is not None and best >= last.price and now - last.sent_at < REPEAT_AFTER:
        return None
    return Deal(route=route, offers=top_offers(ranked, route.top_n), reason="; ".join(reasons))


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
