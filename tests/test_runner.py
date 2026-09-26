from collections import Counter
from datetime import date

from cheap_flights.models import FetchKind, FetchResult, Place, SearchJob
from cheap_flights.runner import RunStats, execute
from tests.helpers import make_offer, make_route


def jobs(count, route_name="r1"):
    route = make_route(name=route_name)
    return [
        SearchJob(route, Place("IST", "IST"), Place("LHR", "LHR"), date(2026, 10, day + 1))
        for day in range(count)
    ]


class ScriptedFetcher:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def search(self, job):
        self.calls += 1
        return self.results.pop(0)


def ok(price):
    return FetchResult(FetchKind.OK, offers=(make_offer(price),))


def run(fetcher, job_list, retries=2, **kwargs):
    sleeps = []
    output = execute(
        job_list,
        fetcher,
        retries=retries,
        delay_seconds=1.0,
        sleep=sleeps.append,
        rand=lambda: 0.5,
        **kwargs,
    )
    return output, sleeps


def test_counts_outcomes_and_collects_offers_by_route():
    fetcher = ScriptedFetcher(
        ok(100), FetchResult(FetchKind.NO_FLIGHTS), FetchResult(FetchKind.PARSE_ERROR)
    )
    output, sleeps = run(fetcher, jobs(3))
    stats = output.stats
    assert (stats.searches, stats.ok, stats.no_flights, stats.failed) == (3, 1, 1, 1)
    assert stats.failures == Counter({"parse_error": 1})
    assert [o.price for o in output.offers["r1"]] == [100]
    assert sleeps == [1.0, 1.0]  # throttle between jobs: delay * (0.7 + 0.6 * 0.5)


def test_retries_blocked_and_network_errors_with_backoff():
    fetcher = ScriptedFetcher(
        FetchResult(FetchKind.BLOCKED), FetchResult(FetchKind.NETWORK_ERROR), ok(90)
    )
    output, sleeps = run(fetcher, jobs(1), retries=2)
    assert fetcher.calls == 3
    assert sleeps == [2.5, 4.5]  # 2**1 + 0.5, 2**2 + 0.5
    assert output.stats.ok == 1


def test_gives_up_after_retries():
    fetcher = ScriptedFetcher(*[FetchResult(FetchKind.BLOCKED)] * 3)
    output, _ = run(fetcher, jobs(1), retries=2)
    assert fetcher.calls == 3
    assert output.stats.failures == Counter({"blocked": 1})


def test_parse_errors_and_consent_are_not_retried():
    fetcher = ScriptedFetcher(FetchResult(FetchKind.CONSENT_WALL))
    run(fetcher, jobs(1), retries=3)
    assert fetcher.calls == 1


def test_stops_between_jobs_when_asked():
    fetcher = ScriptedFetcher(ok(1), ok(2), ok(3))
    output, _ = run(fetcher, jobs(3), should_stop=lambda: fetcher.calls >= 1)
    assert (output.stats.searches, output.stopped) == (1, True)


def test_progress_callback_runs_per_job():
    ticks = []
    run(ScriptedFetcher(ok(1), ok(2)), jobs(2), on_progress=lambda: ticks.append(1))
    assert len(ticks) == 2


def test_failed_run_threshold():
    assert not RunStats().is_failed()
    assert RunStats(searches=10, ok=1, failures=Counter({"blocked": 9})).is_failed() is False
    assert RunStats(searches=10, failures=Counter({"blocked": 7, "parse_error": 3})).is_failed()
    stats = RunStats(searches=10, failures=Counter({"blocked": 7, "parse_error": 3}))
    assert stats.dominant_failure() == "blocked"
    assert stats.summary() == "searches=10 ok=0 no_flights=0 failed=blocked:7,parse_error:3"
