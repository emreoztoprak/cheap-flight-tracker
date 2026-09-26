"""Capture trimmed Google Flights pages into tests/fixtures/google/ for the parser tests.

Makes 5 live requests. Run: ./scripts/dev.sh uv run python scripts/capture_fixtures.py
"""

from dataclasses import replace
from datetime import date, timedelta
from html import escape
from pathlib import Path

import primp
from selectolax.lexbor import LexborHTMLParser

from cheap_flights.config import Route
from cheap_flights.google.client import URL, build_query, make_http_client
from cheap_flights.models import Place, SearchJob

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "google"


def trim(html: str) -> str:
    """Keep only the <title> and the ds:1 results script; real pages are ~2 MB."""
    tree = LexborHTMLParser(html)
    title = tree.css_first("title")
    results = tree.css_first(r"script.ds\:1")
    body = f'<script class="ds:1">{results.text()}</script>' if results else ""
    heading = escape(title.text()) if title else ""
    return f"<html><head><title>{heading}</title></head><body>{body}</body></html>"


def job(destination: Place, day: date, back: date | None = None, stops: str = "any") -> SearchJob:
    route = Route.model_validate(
        {
            "name": "capture",
            "from": "IST",
            "to": [destination.label],
            "stops": stops,
            "alert": {"max_price": 1},
        }
    )
    return SearchJob(route, Place("IST", "IST"), destination, day, back)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    day = date.today() + timedelta(days=30)
    cases = {
        "oneway": build_query(job(Place("LHR", "LHR"), day)),
        "roundtrip": build_query(job(Place("AMS", "AMS"), day, day + timedelta(days=5))),
        "noresults": replace(
            build_query(job(Place("LHR", "LHR"), day, stops="direct")), max_price=5
        ),
        "country": build_query(job(Place("DE", "/m/0345h"), day)),
    }
    http = make_http_client(20)
    for name, query in cases.items():
        response = http.get(URL, params=query.params())
        (OUT / f"{name}.html").write_text(trim(response.text), encoding="utf-8")
        print(f"{name}: HTTP {response.status_code} {len(response.text)} bytes")
    bare = primp.Client(impersonate="chrome_145", impersonate_os="macos")  # no consent cookie
    response = bare.get(URL, params=cases["oneway"].params())
    consent = trim(response.text)
    (OUT / "consent.html").write_text(consent, encoding="utf-8")
    print(f"consent: final URL {response.url}")
    if "Before you continue" not in consent:
        print("WARNING: no consent page (non-EU network?); write consent.html by hand:")
        print("  <html><head><title>Before you continue</title></head><body></body></html>")


if __name__ == "__main__":
    main()
