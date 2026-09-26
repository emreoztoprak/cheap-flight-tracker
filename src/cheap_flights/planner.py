"""Expand routes into individual Google Flights searches, within the per-run budget."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .config import Route
from .models import SearchJob
from .places import ResolvedRoute


@dataclass(frozen=True)
class Plan:
    jobs: tuple[SearchJob, ...]
    requested: int  # searches needed to cover every date
    step: int  # 1 = every date searched, 2 = every 2nd date, ...


def departure_dates(route: Route, today: date) -> list[date]:
    dates = route.window.dates(today)
    if route.weekdays is not None:
        dates = [day for day in dates if day.weekday() in route.weekdays]
    return dates


def _stays(route: Route) -> list[int | None]:
    if route.trip == "one-way":
        return [None]
    low, high = route.nights
    return list(range(low, high + 1))


def _searches_per_date(resolved: ResolvedRoute) -> int:
    return len(resolved.origins) * len(resolved.destinations) * len(_stays(resolved.route))


def build_plan(routes: Sequence[ResolvedRoute], today: date, budget: int) -> Plan:
    dates = [departure_dates(resolved.route, today) for resolved in routes]

    def total(step: int) -> int:
        return sum(
            len(days[::step]) * _searches_per_date(resolved)
            for resolved, days in zip(routes, dates, strict=True)
        )

    requested = total(1)
    longest = max((len(days) for days in dates), default=1)
    step = 1
    while total(step) > budget and step < longest:
        step += 1

    jobs = [
        SearchJob(
            route=resolved.route,
            origin=origin,
            destination=destination,
            depart_date=day,
            return_date=None if stay is None else day + timedelta(days=stay),
        )
        for resolved, days in zip(routes, dates, strict=True)
        for day in days[::step]
        for origin in resolved.origins
        for destination in resolved.destinations
        for stay in _stays(resolved.route)
    ]
    return Plan(jobs=tuple(jobs), requested=requested, step=step)
