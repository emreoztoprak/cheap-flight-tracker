"""Run a job now and then on a cron schedule until asked to stop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

log = logging.getLogger(__name__)


def touch_heartbeat(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError as exc:
        log.warning("cannot update heartbeat file %s: %s", path, exc)


def _run_safely(job: Callable[[], object]) -> None:
    try:
        job()
    except Exception:
        log.exception("run failed unexpectedly; the scheduler keeps going")


Schedule = tuple[str, ZoneInfo]


def run_forever(
    job: Callable[[], object],
    get_schedule: Callable[[], Schedule | None],
    stop: threading.Event,
    heartbeat: Path,
    *,
    now: Callable[[], datetime] | None = None,
    wait: Callable[[float], object] | None = None,
    tick_seconds: float = 5.0,
) -> None:
    """Run `job` as soon as a schedule exists, then at each cron time.

    `get_schedule` is asked on every tick, so a schedule saved from the web UI applies at once;
    it returns None while there is no valid configuration.
    """
    clock = now or (lambda: datetime.now(UTC))
    pause = wait or stop.wait
    started = False
    while not stop.is_set():
        touch_heartbeat(heartbeat)
        schedule = get_schedule()
        if schedule is None:
            pause(tick_seconds)
            continue
        if not started:
            started = True
            _run_safely(job)
            continue
        cron, tz = schedule
        due = croniter(cron, clock().astimezone(tz)).get_next(datetime)
        log.info("next run at %s", due.isoformat())
        while not stop.is_set():
            touch_heartbeat(heartbeat)
            if get_schedule() != schedule:
                break  # changed in the UI: work out the next run again
            remaining = (due - clock()).total_seconds()
            if remaining <= 0:
                _run_safely(job)
                break
            pause(min(tick_seconds, remaining))
