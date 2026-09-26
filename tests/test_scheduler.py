import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cheap_flights.scheduler import run_forever

UTC = ZoneInfo("UTC")


def fake_clock(start):
    clock = {"now": start}

    def wait(seconds):
        clock["now"] += timedelta(seconds=seconds)

    return clock, (lambda: clock["now"]), wait


def test_runs_immediately_then_on_schedule(tmp_path):
    clock, now, wait = fake_clock(datetime(2026, 9, 26, 10, 0, 30, tzinfo=UTC))
    stop, runs = threading.Event(), []

    def job():
        runs.append(clock["now"].strftime("%H:%M"))
        if len(runs) == 3:
            stop.set()

    run_forever(job, "0 * * * *", UTC, stop, tmp_path / "hb", now=now, wait=wait, tick_seconds=600)
    assert runs == ["10:00", "11:00", "12:00"]
    assert (tmp_path / "hb").exists()


def test_a_crashing_run_does_not_stop_the_scheduler(tmp_path):
    _, now, wait = fake_clock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC))
    stop, calls = threading.Event(), []

    def job():
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("boom")

    run_forever(job, "*/5 * * * *", UTC, stop, tmp_path / "hb", now=now, wait=wait)
    assert len(calls) == 2


def test_stop_before_start_skips_everything(tmp_path):
    stop = threading.Event()
    stop.set()
    calls = []
    run_forever(lambda: calls.append(1), "* * * * *", UTC, stop, tmp_path / "hb")
    assert calls == []
