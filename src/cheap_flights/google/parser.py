"""Extract flight offers from a Google Flights results page.

Results are embedded in <script class="ds:1"> as AF_initDataCallback({... data:[...], ...}).
Payload layout (verified 2026-09-26):
  payload[2][0]  "best flights" items      payload[3][0]  "other flights" items
  item[0]        flight: [1] airline names, [2] legs
  item[1][0][1]  price (None when Google shows "price unavailable")
  leg            [3] from IATA, [6] to IATA, [8] departure time, [10] arrival time,
                 [20] departure date [y, m, d], [21] arrival date, [22] [airline code, number, ...]
Google drops zero time components: [8] is 08:00, [None, 31] is 00:31, None is 00:00.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from ..models import Leg, Offer, SearchJob

_SECTIONS = (2, 3)


class ParseError(Exception):
    """The page is not a results page we understand (Google probably changed it)."""


def parse_offers(html: str, job: SearchJob, url: str) -> list[Offer]:
    payload = _payload(html)
    if payload is None:
        return []
    items = [entry for index in _SECTIONS for entry in _section(payload, index)]
    offers: list[Offer] = []
    unreadable = priceless = 0
    for entry in items:
        try:
            offer = _offer(entry, job, url)
        except (IndexError, TypeError, ValueError, KeyError):
            unreadable += 1
            continue
        if offer is None:
            priceless += 1
        else:
            offers.append(offer)
    if items and unreadable == len(items):
        raise ParseError(f"could not read any of {len(items)} flights")
    if items and not offers:
        # Every flight without a price almost always means the price moved in the layout;
        # returning "no flights" would hide the breakage from the health monitor.
        raise ParseError(f"none of {len(items)} flights has a price")
    return _dedupe(offers)


def _payload(html: str) -> list[Any] | None:
    """Return the results payload, or None when Google reports no results."""
    node = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if node is None:
        raise ParseError("results script (ds:1) not found")
    script = node.text()
    if "data:" not in script:
        raise ParseError("results script has no data")
    data = script.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.rstrip().endswith("errorHasStatus: true"):
        return None
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise ParseError(f"results data is not JSON: {exc}") from exc
    if not isinstance(payload, list) or len(payload) < 4:
        raise ParseError("unexpected results structure")
    return payload


def _section(payload: list[Any], index: int) -> list[Any]:
    """Items of one result list; a genuine no-results page has null in both sections."""
    block = payload[index] if len(payload) > index else None
    if block is None:
        return []
    if not isinstance(block, list) or (block and not isinstance(block[0], list | None)):
        raise ParseError(f"unexpected results section {index}: {type(block).__name__}")
    return block[0] if block and block[0] else []


def _offer(entry: list[Any], job: SearchJob, url: str) -> Offer | None:
    flight = entry[0]
    price_block = entry[1]
    price = price_block[0][1] if price_block and price_block[0] else None
    if price is None:
        return None
    legs = tuple(_leg(raw) for raw in flight[2])
    if not legs:
        raise ValueError("flight without legs")
    return Offer(
        route=job.route.name,
        destination=job.destination.label,
        price=int(price),
        currency=job.route.currency,
        airlines=tuple(flight[1] or ()),
        legs=legs,
        depart_date=job.depart_date,
        return_date=job.return_date,
        url=url,
    )


def _leg(raw: list[Any]) -> Leg:
    return Leg(
        origin=str(raw[3]),
        destination=str(raw[6]),
        depart=_moment(raw[20], raw[8]),
        arrive=_moment(raw[21], raw[10]),
        airline=str(raw[22][0]),
        flight_number=str(raw[22][1]),
    )


def _moment(day: list[int], clock: list[int | None] | None) -> datetime:
    padded = [*(clock or []), None, None]
    return datetime(day[0], day[1], day[2], padded[0] or 0, padded[1] or 0)


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: set[tuple[object, ...]] = set()
    unique: list[Offer] = []
    for offer in sorted(offers, key=lambda o: o.price):
        key = (
            offer.price,
            tuple((leg.airline, leg.flight_number, leg.depart) for leg in offer.legs),
        )
        if key not in seen:
            seen.add(key)
            unique.append(offer)
    return unique
