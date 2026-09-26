from datetime import date

from cheap_flights.planner import build_plan, departure_dates
from tests.helpers import make_resolved, make_route

TODAY = date(2026, 9, 26)  # a Saturday


def test_one_way_searches_every_date():
    rr = make_resolved(make_route(trip="one-way", window={"next_days": 3}))
    plan = build_plan([rr], TODAY, budget=100)
    assert [(j.depart_date, j.return_date) for j in plan.jobs] == [
        (date(2026, 9, 27), None),
        (date(2026, 9, 28), None),
        (date(2026, 9, 29), None),
    ]
    assert (plan.requested, plan.step) == (3, 1)


def test_round_trip_pairs_each_date_with_each_stay():
    rr = make_resolved(
        make_route(nights="2-3", window={"next_days": 1}), destinations=("LHR", "AMS")
    )
    plan = build_plan([rr], TODAY, budget=100)
    assert {(j.destination.code, j.depart_date, j.return_date) for j in plan.jobs} == {
        ("LHR", date(2026, 9, 27), date(2026, 9, 29)),
        ("LHR", date(2026, 9, 27), date(2026, 9, 30)),
        ("AMS", date(2026, 9, 27), date(2026, 9, 29)),
        ("AMS", date(2026, 9, 27), date(2026, 9, 30)),
    }


def test_weekdays_filter():
    route = make_route(trip="one-way", window={"next_days": 7}, weekdays=["sat"])
    assert departure_dates(route, TODAY) == [date(2026, 10, 3)]


def test_budget_thins_dates_evenly():
    rr = make_resolved(
        make_route(trip="one-way", window={"next_days": 10}), destinations=("LHR", "AMS")
    )
    plan = build_plan([rr], TODAY, budget=10)
    assert (plan.requested, plan.step, len(plan.jobs)) == (20, 2, 10)
    assert sorted({j.depart_date for j in plan.jobs}) == [
        date(2026, 9, 27),
        date(2026, 9, 29),
        date(2026, 10, 1),
        date(2026, 10, 3),
        date(2026, 10, 5),
    ]


def test_budget_that_cannot_be_met_keeps_one_date_per_route():
    rr = make_resolved(
        make_route(trip="one-way", window={"next_days": 10}), destinations=("LHR", "AMS")
    )
    plan = build_plan([rr], TODAY, budget=1)
    assert (plan.requested, plan.step, len(plan.jobs)) == (20, 10, 2)


def test_past_window_produces_no_jobs():
    rr = make_resolved(make_route(window={"from": "2026-09-01", "to": "2026-09-10"}))
    plan = build_plan([rr], TODAY, budget=100)
    assert (plan.jobs, plan.requested, plan.step) == ((), 0, 1)


def test_weekdays_matching_no_date_produces_no_jobs():
    route = make_route(trip="one-way", window={"next_days": 3}, weekdays=["wed"])
    assert build_plan([make_resolved(route)], TODAY, budget=100).jobs == ()
