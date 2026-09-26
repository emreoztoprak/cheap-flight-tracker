"""Render price reports and health events as channel-neutral Messages."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Route
from .evaluator import Report
from .health import EventKind, HealthEvent
from .models import Offer
from .money import money
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


MEDALS = ("🥇", "🥈", "🥉")


def _arrival(offer: Offer) -> str:
    first, last = offer.legs[0], offer.legs[-1]
    days_later = (last.arrive.date() - first.depart.date()).days
    return f"{last.arrive:%H:%M}" + (f"+{days_later}" if days_later > 0 else "")


def _option_lines(rank: int, offer: Offer, show_airports: bool) -> tuple[Line, ...]:
    marker = MEDALS[rank - 1] if rank <= len(MEDALS) else f"{rank}."
    heading = f"{marker} {money(offer.price, offer.currency)} · {offer.depart_date:%a %d %b}"
    if offer.return_date is not None:
        nights = (offer.return_date - offer.depart_date).days
        heading += f" → {offer.return_date:%a %d %b} ({nights} night{'s' if nights != 1 else ''})"
    first, last = offer.legs[0], offer.legs[-1]
    details = [", ".join(offer.airlines) or first.airline, _stops(offer)]
    details.append(f"{first.depart:%H:%M} → {_arrival(offer)}")
    if show_airports:
        details.insert(0, f"{first.origin}→{last.destination}")
    return (
        Line(heading, style="option"),
        Line(" · ".join(details), style="detail"),
        Line("View on Google Flights ›", offer.url, style="link"),
    )


def change_text(report: Report) -> str:
    """The change since the previous check, in words (also stored in the History page)."""
    if report.previous is None:
        return "first check"
    change = report.change
    if change is None:
        return ""
    if change == 0:
        return "same as last check"
    currency = report.route.currency
    sign = "+" if change > 0 else "−"
    was = money(report.previous, currency)
    return f"{sign}{money(abs(change), currency)} since last check (was {was})"


def _change_line(report: Report) -> str:
    if report.previous is None:
        return "🆕 First check"
    change = report.change or 0
    icon = "📈" if change > 0 else "📉" if change < 0 else "➖"
    text = change_text(report)
    return f"{icon} {text[0].upper()}{text[1:]}"


def report_message(
    report: Report,
    *,
    places: str = "",
    show_airports: bool = False,
    checked: datetime | None = None,
    next_check: datetime | None = None,
) -> Message:
    """The message sent after every check, one per route."""
    route = report.route
    footer = f"Checked {checked:%a %d %b %H:%M}" if checked else ""
    if checked and next_check:
        footer += f" · next check {next_check:%a %d %b %H:%M}"
    summary = _route_summary(route, places)
    if not report.offers:
        return Message(
            title=f"✈️ {route.name} — no flights found",
            lines=(
                Line(f"{report.searches} searches, nothing matched your filters"),
                Line(summary, style="note"),
            ),
            footer=footer,
        )
    icon = "🔥" if report.highlights else "✈️"
    best = report.offers[0]
    lines = [Line(_change_line(report))]
    lines += [Line(f"🎯 {highlight}") for highlight in report.highlights]
    if best.return_date is not None:
        summary += " · price for both ways"
    lines.append(Line(summary, style="note"))
    for rank, offer in enumerate(report.offers, start=1):
        lines.append(Line("", style="gap"))
        lines += _option_lines(rank, offer, show_airports)
    return Message(
        title=f"{icon} {route.name} — {money(best.price, route.currency)}",
        lines=tuple(lines),
        footer=footer,
    )


def _route_summary(route: Route, places: str) -> str:
    trip = "one way" if route.trip == "one-way" else "round trip"
    return f"{places or f'{route.origin} → {", ".join(route.to)}'} · {trip}"


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
