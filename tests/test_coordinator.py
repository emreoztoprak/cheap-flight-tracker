import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import yaml

from cheap_flights.coordinator import Coordinator
from cheap_flights.models import FetchKind, FetchResult
from cheap_flights.settings_io import SettingsFiles
from cheap_flights.store import Store
from tests.helpers import make_offer

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
RAW = {
    "schedule": "0 8 * * *",
    "timezone": "Europe/Madrid",
    "search": {"delay_seconds": 0, "retries": 0},
    "routes": [
        {
            "name": "r1",
            "from": "IST",
            "to": ["LHR"],
            "trip": "one-way",
            "window": {"next_days": 2},
            "alert": {"max_price": 100},
        }
    ],
    "notify": {"telegram": {"bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "1"}},
}
ENV = {"TELEGRAM_BOT_TOKEN": "123456:abcdefghij"}


class Inbox:
    name = "inbox"

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class Fetcher:
    def __init__(self, gate=None):
        self.gate, self.calls = gate, 0

    def search(self, job):
        self.calls += 1
        if self.gate:
            self.gate.wait(5)
        return FetchResult(FetchKind.OK, (make_offer(80, depart=job.depart_date),))


def make(tmp_path, raw=RAW, fetcher=None, **kwargs):
    files = SettingsFiles(tmp_path / "config.yaml", base_env={})
    if raw is not None:
        assert files.save(raw, ENV) == []
    inbox, fetcher = Inbox(), fetcher or Fetcher()
    coordinator = Coordinator(
        files,
        Store(":memory:"),
        fetcher_factory=lambda config: fetcher,
        notifier_factory=lambda config: [inbox],
        clock=lambda: NOW,
        sleep=lambda s: None,
        **kwargs,
    )
    coordinator.reload()
    return coordinator, files, inbox, fetcher


def test_without_config_everything_waits(tmp_path):
    coordinator, *_ = make(tmp_path, raw=None)
    assert coordinator.loaded is None
    assert "no configuration yet" in coordinator.error
    assert coordinator.schedule() is None
    assert coordinator.start_run() == "no_config"


def test_valid_config_gives_schedule_and_runs(tmp_path):
    coordinator, _, inbox, fetcher = make(tmp_path)
    assert coordinator.schedule() == ("0 8 * * *", ZoneInfo("Europe/Madrid"))
    stats = coordinator.run("scheduled")
    assert stats.searches == 2 and fetcher.calls == 2
    assert inbox.messages[0].title.startswith("🔥 r1: 80 EUR")
    assert coordinator.store.last_run().trigger == "scheduled"


def test_only_one_run_at_a_time_and_progress_is_visible(tmp_path):
    gate = threading.Event()
    coordinator, *_ = make(tmp_path, fetcher=Fetcher(gate))
    assert coordinator.start_run() == "started"
    for _ in range(100):
        if coordinator.progress().total:
            break
        threading.Event().wait(0.01)
    progress = coordinator.progress()
    assert progress.running and progress.total == 2 and progress.trigger == "manual"
    assert coordinator.start_run() == "busy"
    assert coordinator.run("scheduled") is None
    gate.set()
    coordinator.wait_idle(5)
    assert not coordinator.progress().running
    assert coordinator.progress().done == 2


def test_broken_file_keeps_last_good_config(tmp_path):
    coordinator, files, *_ = make(tmp_path)
    (tmp_path / "config.yaml").write_text("routes: [")
    coordinator.reload()
    assert coordinator.loaded is not None and coordinator.schedule() is not None
    assert "not valid YAML" in coordinator.error


def test_saving_applies_and_reconfigures(tmp_path):
    seen = []
    coordinator, *_ = make(tmp_path, on_config=seen.append)
    assert coordinator.save({**RAW, "schedule": "*/30 * * * *"}, {}) == []
    assert coordinator.schedule()[0] == "*/30 * * * *"
    assert seen[-1].schedule == "*/30 * * * *"
    errors = coordinator.save({**RAW, "schedule": "nope"}, {})
    assert errors and errors[0].path == "schedule"
    assert coordinator.schedule()[0] == "*/30 * * * *"
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["schedule"] == "*/30 * * * *"


def test_save_text_applies(tmp_path):
    coordinator, files, *_ = make(tmp_path)
    text = files.raw_text().replace("0 8 * * *", "0 9 * * *")
    assert coordinator.save_text(text) == []
    assert coordinator.schedule()[0] == "0 9 * * *"


def test_missing_config_is_not_logged_as_an_error(tmp_path, caplog):
    import logging

    caplog.set_level(logging.INFO)
    make(tmp_path, raw=None)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("waiting for setup" in r.getMessage() for r in caplog.records)
