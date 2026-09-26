"""Fetch Google Flights result pages and turn them into FetchResults."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import primp
from fast_flights import FlightQuery, Passengers, Query, create_query

from ..models import FetchKind, FetchResult, SearchJob
from .parser import ParseError, parse_offers

log = logging.getLogger(__name__)

URL = "https://www.google.com/travel/flights"
# Pre-accepted cookie consent. Without it, EU IPs are redirected to consent.google.com.
CONSENT_COOKIES = {
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg",
    "CONSENT": "YES+",
}


class HttpResponse(Protocol):
    status_code: int
    url: str
    text: str


class HttpClient(Protocol):
    def get(self, url: str, params: dict[str, str]) -> HttpResponse: ...


def make_http_client(timeout: float) -> HttpClient:
    client = primp.Client(
        impersonate="chrome_145",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
        timeout=timeout,
    )
    client.set_cookies("https://www.google.com", CONSENT_COOKIES)
    return client


def build_query(job: SearchJob) -> Query:
    route = job.route
    earliest, latest = route.depart_time if route.depart_time else (None, None)
    flights = [
        FlightQuery(
            date=job.depart_date.isoformat(),
            from_airport=job.origin.code,
            to_airport=job.destination.code,
            max_stops=route.max_stops,
            earliest_departure_hour=earliest,
            latest_departure_hour=latest,
        )
    ]
    if job.return_date is not None:
        flights.append(
            FlightQuery(
                date=job.return_date.isoformat(),
                from_airport=job.destination.code,
                to_airport=job.origin.code,
                max_stops=route.max_stops,
            )
        )
    return create_query(
        flights=flights,
        trip="round-trip" if job.return_date is not None else "one-way",
        passengers=Passengers(adults=route.passengers.adults, children=route.passengers.children),
        language="en",
        currency=route.currency,
    )


def classify(status: int, url: str, html: str) -> tuple[FetchKind, str] | None:
    """Return (kind, detail) for a response that is not a results page, else None."""
    if "consent.google." in url or "<title>Before you continue" in html:
        return FetchKind.CONSENT_WALL, "redirected to Google's cookie-consent page"
    if (
        status in (403, 429)
        or "/sorry/" in url
        or ("unusual traffic" in html and "ds:1" not in html)
    ):
        return FetchKind.BLOCKED, f"Google refused the request (HTTP {status})"
    if status >= 500:
        return FetchKind.NETWORK_ERROR, f"Google returned HTTP {status}"
    if status != 200:
        return FetchKind.NETWORK_ERROR, f"unexpected HTTP {status}"
    return None


class GoogleFlights:
    def __init__(self, http: HttpClient, debug_dir: Path | None = None, keep_debug: int = 5):
        self._http = http
        self._debug_dir = debug_dir
        self._keep_debug = max(1, keep_debug)

    def search(self, job: SearchJob) -> FetchResult:
        started = time.monotonic()
        result = self._search(job)
        log.debug(
            "search %s %s→%s %s%s: %s (%d offers, %.1fs) %s",
            job.route.name,
            job.origin.label,
            job.destination.label,
            job.depart_date,
            f"/{job.return_date}" if job.return_date else "",
            result.kind,
            len(result.offers),
            time.monotonic() - started,
            result.detail,
        )
        return result

    def _search(self, job: SearchJob) -> FetchResult:
        query = build_query(job)
        try:
            response = self._http.get(URL, params=query.params())
        except Exception as exc:  # primp raises its own types for DNS, TLS and timeout errors
            return FetchResult(FetchKind.NETWORK_ERROR, detail=f"{type(exc).__name__}: {exc}")
        html = response.text
        verdict = classify(response.status_code, str(response.url), html)
        if verdict is not None:
            return FetchResult(verdict[0], detail=verdict[1])
        try:
            offers = parse_offers(html, job, query.url())
        except ParseError as exc:
            self._save_debug(job, html)
            return FetchResult(FetchKind.PARSE_ERROR, detail=str(exc))
        if not offers:
            return FetchResult(FetchKind.NO_FLIGHTS)
        return FetchResult(FetchKind.OK, offers=tuple(offers))

    def _save_debug(self, job: SearchJob, html: str) -> None:
        if self._debug_dir is None:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            name = re.sub(
                r"[^A-Za-z0-9]+", "_", f"{job.route.name}-{job.destination.code}-{job.depart_date}"
            )
            (self._debug_dir / f"{stamp}-{name}.html").write_text(html, encoding="utf-8")
            for old in sorted(self._debug_dir.glob("*.html"))[: -self._keep_debug]:
                old.unlink()
        except OSError as exc:
            log.warning("could not save debug HTML to %s: %s", self._debug_dir, exc)
