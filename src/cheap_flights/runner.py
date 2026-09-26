"""Execute search jobs one at a time with throttling and retries, counting outcomes."""

from __future__ import annotations

import logging
import random
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .models import FetchKind, FetchResult, Offer, SearchJob

log = logging.getLogger(__name__)

FAILED_RUN_RATIO = 0.9
_RETRYABLE = frozenset({FetchKind.NETWORK_ERROR, FetchKind.BLOCKED})


class Fetcher(Protocol):
    def search(self, job: SearchJob) -> FetchResult: ...


@dataclass
class RunStats:
    searches: int = 0
    ok: int = 0
    no_flights: int = 0
    failures: Counter[str] = field(default_factory=Counter)

    @property
    def failed(self) -> int:
        return sum(self.failures.values())

    def is_failed(self) -> bool:
        return self.searches > 0 and self.failed / self.searches > FAILED_RUN_RATIO

    def dominant_failure(self) -> str | None:
        most_common = self.failures.most_common(1)
        return most_common[0][0] if most_common else None

    def summary(self) -> str:
        failed = ",".join(f"{kind}:{count}" for kind, count in sorted(self.failures.items()))
        return (
            f"searches={self.searches} ok={self.ok} no_flights={self.no_flights} "
            f"failed={failed or 0}"
        )


@dataclass
class RunOutput:
    stats: RunStats
    offers: dict[str, list[Offer]]
    stopped: bool = False
    answered: Counter[str] = field(default_factory=Counter)  # searches per route Google answered


def execute(
    jobs: Sequence[SearchJob],
    fetcher: Fetcher,
    *,
    retries: int,
    delay_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    should_stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[], None] = lambda: None,
) -> RunOutput:
    output = RunOutput(stats=RunStats(), offers=defaultdict(list))
    stats = output.stats
    for index, job in enumerate(jobs):
        if should_stop():
            output.stopped = True
            break
        if index:
            sleep(delay_seconds * (0.7 + 0.6 * rand()))
        result = _search_with_retry(job, fetcher, retries, sleep, rand)
        stats.searches += 1
        if result.kind is FetchKind.OK:
            stats.ok += 1
            output.answered[job.route.name] += 1
            output.offers[job.route.name].extend(result.offers)
        elif result.kind is FetchKind.NO_FLIGHTS:
            stats.no_flights += 1
            output.answered[job.route.name] += 1
        else:
            stats.failures[str(result.kind)] += 1
            log.warning(
                "search failed: %s %s→%s %s: %s %s",
                job.route.name,
                job.origin.label,
                job.destination.label,
                job.depart_date,
                result.kind,
                result.detail,
            )
        on_progress()
    output.offers = dict(output.offers)
    return output


def _search_with_retry(
    job: SearchJob,
    fetcher: Fetcher,
    retries: int,
    sleep: Callable[[float], None],
    rand: Callable[[], float],
) -> FetchResult:
    for attempt in range(retries + 1):
        result = fetcher.search(job)
        if result.kind not in _RETRYABLE or attempt == retries:
            return result
        wait = 2 ** (attempt + 1) + rand()
        log.warning(
            "retrying %s→%s %s after %s (%s), attempt %d/%d in %.1fs",
            job.origin.label,
            job.destination.label,
            job.depart_date,
            result.kind,
            result.detail,
            attempt + 1,
            retries,
            wait,
        )
        sleep(wait)
    raise AssertionError("unreachable")
