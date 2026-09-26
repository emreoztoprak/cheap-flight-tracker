"""Render price reports and health events as channel-neutral Messages."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .config import Route
from .evaluator import Report
from .health import EventKind, HealthEvent
from .models import Offer
from .notify.base import Line, Message


def _stops(offer: Offer) -> str:
    if offer.stops == 0:
        return "direct"
    via = ", ".join(leg.destination for leg in offer.legs[:-1])
    return f"{offer.stops} stop{'s' if offer.stops > 1 else ''} ({via})"


def offer_summary(offer: Offer) -> str:
    """One-line description: price, airports, times, airlines, stops, return date."""
    first, last = offer.legs[0], offer.legs[-1]
    days_later = (last.arrive.date() - first.depart.date()).days
    arrival = f"{last.arrive:%H:%M}" + (f"+{days_later}" if days_later > 0 else "")
    airlines = ", ".join(offer.airlines) or first.airline
    text = (
        f"{offer.price} {offer.currency}  {first.origin}→{last.destination}  "
        f"{first.depart:%a %d %b %H:%M}→{arrival}  {airlines}, {_stops(offer)}"
    )
    if offer.return_date is not None:
        text += f"  · return {offer.return_date:%a %d %b}"
    return text


def offer_line(index: int, offer: Offer) -> Line:
    return Line(text=f"{index}. {offer_summary(offer)}", url=offer.url)


def change_text(report: Report) -> str:
    currency = report.route.currency
    if report.previous is None:
        return "first check"
    change = report.change
    if change is None:
        return ""
    if change == 0:
        return "= same as last check"
    arrow, sign = ("↑", "+") if change > 0 else ("↓", "−")
    return f"{arrow} {sign}{abs(change)} {currency} since last check ({report.previous} {currency})"


def report_message(report: Report) -> Message:
    """The message sent after every check, one per route."""
    route = report.route
    if not report.offers:
        return Message(
            title=f"✈️ {route.name}: no flights found this check ({report.searches} searches)",
            lines=(Line(_route_summary(route)),),
        )
    icon = "🔥" if report.highlights else "✈️"
    title = f"{icon} {route.name}: {report.best_price} {route.currency}"
    if report.highlights:
        title += " — " + "; ".join(report.highlights)
    title += f"  {change_text(report)}"
    best = report.offers[0]
    footer = "Round-trip prices are the total for both directions." if best.return_date else ""
    lines = (Line(_route_summary(route)),) + tuple(
        offer_line(index, offer) for index, offer in enumerate(report.offers, start=1)
    )
    return Message(title=title, lines=lines, footer=footer)


def _route_summary(route: Route) -> str:
    trip = "one way" if route.trip == "one-way" else "round trip"
    return f"{route.origin} → {', '.join(route.to)} · {trip}"


def check_message() -> Message:
    return Message(
        title="✅ Cheap Flight Tracker test message",
        lines=(Line("If you can read this, notifications work."),),
    )


CAUSE_HINTS = {
    "consent_wall": (
        "Google showed its cookie-consent page; the built-in consent cookie is no longer "
        "accepted and the client needs an update."
    ),
    "blocked": (
        "Google is rate-limiting or showing a CAPTCHA. Raise search.delay_seconds, lower "
        "search.max_searches_per_run, or wait."
    ),
    "parse_error": (
        "Google probably changed its page format; the parser needs an update. "
        "The page is saved in /data/debug."
    ),
    "network_error": "The container can't reach Google (network or DNS problem).",
    "internal_error": "A bug in Cheap Flight Tracker; see the container logs for the traceback.",
}


def health_message(event: HealthEvent, tz: ZoneInfo) -> Message:
    if event.kind is EventKind.RECOVERED:
        return Message(
            title="✅ Cheap Flight Tracker: fetching flight data works again",
            lines=(Line(f"Recovered after {event.failed_runs} failed run(s)."),),
        )
    last = (
        event.last_success_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        if event.last_success_at
        else "never"
    )
    cause = event.cause or "unknown"
    suffix = " (still failing)" if event.kind is EventKind.REMINDER else ""
    return Message(
        title=f"⚠️ Cheap Flight Tracker: I can't fetch flight data{suffix}",
        lines=(
            Line(
                f"{event.failed_runs} runs in a row failed "
                f"({event.failures}/{event.searches} searches failed in the last run)."
            ),
            Line(f"Cause: {cause}. {CAUSE_HINTS.get(cause, 'See the container logs.')}"),
            Line(f"Last successful run: {last}"),
        ),
    )
