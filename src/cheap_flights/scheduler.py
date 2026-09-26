"""Run a job now and then on a cron schedule until asked to stop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
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


def run_forever(
    job: Callable[[], object],
    schedule: str,
    tz: ZoneInfo,
    stop: threading.Event,
    heartbeat: Path,
    *,
    now: Callable[[], datetime] | None = None,
    wait: Callable[[float], object] | None = None,
    tick_seconds: float = 30.0,
) -> None:
    clock = now or (lambda: datetime.now(tz))
    pause = wait or stop.wait
    if stop.is_set():
        return
    touch_heartbeat(heartbeat)
    _run_safely(job)
    while not stop.is_set():
        due = croniter(schedule, clock()).get_next(datetime)
        log.info("next run at %s", due.isoformat())
        while not stop.is_set():
            touch_heartbeat(heartbeat)
            remaining = (due - clock()).total_seconds()
            if remaining <= 0:
                break
            pause(min(tick_seconds, remaining))
        if not stop.is_set():
            _run_safely(job)
